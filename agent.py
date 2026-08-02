#!/usr/bin/env python3
"""
agent.py

The self-correcting conversion agent. Wraps the plain generate-once
logic in pyspark_converter.py with a generate -> validate -> repair
loop, and can export the final result as a Databricks-importable
.ipynb notebook.

Loop:
    1. Generate PySpark code from the source script (via Ollama)
    2. Validate the generated code with ast.parse() (catches syntax errors)
    3. If invalid: send the broken code + the exact error back to the
       model, ask it to fix it, and retry (up to max_attempts)
    4. If a human-reported runtime error comes back later (e.g. from
       running the notebook in Databricks), the same repair step can
       be invoked directly with that error message.
"""

import ast
import json
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import ollama

from pyspark_converter import build_prompt, extract_code, BASE_SYSTEM_PROMPT, LANGUAGE_HINTS


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_syntax(code: str) -> tuple[bool, Optional[str]]:
    """
    Returns (is_valid, error_message).
    Uses ast.parse() -- fast, no PySpark/Java required, catches genuine
    Python syntax errors (the most common failure mode for local models).
    """
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        error_msg = f"SyntaxError: {e.msg} (line {e.lineno}, column {e.offset})"
        return False, error_msg


# --------------------------------------------------------------------------
# Repair prompting
# --------------------------------------------------------------------------

def build_repair_prompt(original_messages: list[dict], broken_code: str, error: str) -> list[dict]:
    """
    Appends the failed attempt and its error to the conversation, asking
    the model to fix it. Keeps full context of the original request.
    """
    messages = list(original_messages)
    messages.append({"role": "assistant", "content": broken_code})
    messages.append({
        "role": "user",
        "content": f"""That code has a problem when checked:

{error}

Fix the code so it is valid and correct. Output ONLY the corrected PySpark \
code -- no explanations, no markdown fences, no commentary.""",
    })
    return messages


# --------------------------------------------------------------------------
# The agent loop
# --------------------------------------------------------------------------

def run_agent(
    source_code: str,
    file_suffix: str,
    model: str,
    host: Optional[str] = None,
    max_attempts: int = 3,
    on_event: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Runs the generate -> validate -> repair loop.

    on_event(event_dict) is called at each step for live progress reporting
    (used by the web UI to stream stage updates). event_dict always has a
    "type" key, one of:
        "attempt_start"   {attempt, max_attempts}
        "token"           {attempt, chunk, accumulated_len}
        "validating"      {attempt}
        "attempt_failed"  {attempt, error}
        "attempt_passed"  {attempt}
        "done"            {attempts_used, final_code}
        "exhausted"       {attempts_used, last_error, final_code}

    Returns a dict:
        {
            "success": bool,               # True if syntax-valid on the final attempt
            "code": str,                    # final code (best attempt)
            "attempts": int,                # number of attempts used
            "history": [ {attempt, code, valid, error}, ... ],
            "last_error": str | None,
        }
    """
    client = ollama.Client(host=host) if host else ollama.Client()
    messages = build_prompt(source_code, file_suffix)

    history = []
    final_code = ""
    last_error = None

    for attempt in range(1, max_attempts + 1):
        if on_event:
            on_event({"type": "attempt_start", "attempt": attempt, "max_attempts": max_attempts})

        # On retries, use the repair prompt built from the previous failure
        if attempt > 1:
            messages = build_repair_prompt(messages[:2], final_code, last_error)

        accumulated = ""
        stream = client.chat(
            model=model,
            messages=messages,
            options={"temperature": 0.1},
            stream=True,
        )
        for chunk in stream:
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                accumulated += piece
                if on_event:
                    on_event({"type": "token", "attempt": attempt, "chunk": piece, "accumulated_len": len(accumulated)})

        code = extract_code(accumulated)
        final_code = code

        if on_event:
            on_event({"type": "validating", "attempt": attempt})

        is_valid, error = validate_syntax(code)
        history.append({"attempt": attempt, "code": code, "valid": is_valid, "error": error})

        if is_valid:
            if on_event:
                on_event({"type": "attempt_passed", "attempt": attempt})
                on_event({"type": "done", "attempts_used": attempt, "final_code": code})
            return {
                "success": True,
                "code": code,
                "attempts": attempt,
                "history": history,
                "last_error": None,
            }

        last_error = error
        if on_event:
            on_event({"type": "attempt_failed", "attempt": attempt, "error": error})

    # Exhausted all attempts -- return the best (last) attempt anyway,
    # so the user still has something to look at/fix manually.
    if on_event:
        on_event({"type": "exhausted", "attempts_used": max_attempts, "last_error": last_error, "final_code": final_code})
    return {
        "success": False,
        "code": final_code,
        "attempts": max_attempts,
        "history": history,
        "last_error": last_error,
    }


def repair_with_external_error(
    source_code: str,
    file_suffix: str,
    broken_code: str,
    external_error: str,
    model: str,
    host: Optional[str] = None,
    on_event: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Used when a human reports a runtime error from actually running the
    code (e.g. in Databricks) that our local ast.parse() check couldn't
    catch. One repair attempt using that real error message.
    """
    client = ollama.Client(host=host) if host else ollama.Client()
    original_messages = build_prompt(source_code, file_suffix)
    messages = build_repair_prompt(original_messages, broken_code, external_error)

    if on_event:
        on_event({"type": "attempt_start", "attempt": 1, "max_attempts": 1})

    accumulated = ""
    stream = client.chat(model=model, messages=messages, options={"temperature": 0.1}, stream=True)
    for chunk in stream:
        piece = chunk.get("message", {}).get("content", "")
        if piece:
            accumulated += piece
            if on_event:
                on_event({"type": "token", "attempt": 1, "chunk": piece, "accumulated_len": len(accumulated)})

    code = extract_code(accumulated)
    is_valid, error = validate_syntax(code)

    if on_event:
        if is_valid:
            on_event({"type": "attempt_passed", "attempt": 1})
            on_event({"type": "done", "attempts_used": 1, "final_code": code})
        else:
            on_event({"type": "exhausted", "attempts_used": 1, "last_error": error, "final_code": code})

    return {
        "success": is_valid,
        "code": code,
        "attempts": 1,
        "history": [{"attempt": 1, "code": code, "valid": is_valid, "error": error}],
        "last_error": error,
    }


# --------------------------------------------------------------------------
# Notebook export
# --------------------------------------------------------------------------

def _code_cell(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def _markdown_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def build_notebook(
    original_filename: str,
    source_code: str,
    pyspark_code: str,
    model: str,
    attempts: int,
    validation_note: Optional[str] = None,
) -> dict:
    """
    Builds a Databricks-importable .ipynb structure (plain dict,
    JSON-serializable -- no external nbformat dependency needed).
    """
    cells = []

    cells.append(_markdown_cell(
        f"# PySpark conversion: `{original_filename}`\n\n"
        f"Generated locally with **{model}** via Ollama.  \n"
        f"Attempts used: {attempts}.  \n"
        f"Validated locally with `ast.parse()` "
        f"{'(passed)' if attempts else ''}.\n\n"
        f"Run the cells below on a Databricks serverless cluster to confirm "
        f"the logic executes correctly. If a cell errors, copy the error "
        f"message back into the Spark Convert app to have the agent repair it."
    ))

    cells.append(_markdown_cell("## Original source\n\nFor reference -- not executed."))
    lang = "python" if original_filename.endswith((".py", ".r")) else "sql"
    cells.append(_markdown_cell(f"```{lang}\n{source_code}\n```"))

    cells.append(_markdown_cell("## Converted PySpark code"))
    cells.append(_code_cell(pyspark_code))

    if validation_note:
        cells.append(_markdown_cell(f"## Notes\n\n{validation_note}"))

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }
    return notebook


def build_html_report(
    original_filename: str,
    source_code: str,
    pyspark_code: str,
    model: str,
    attempts: int,
    history: list[dict],
) -> str:
    """
    A simple standalone HTML view of the conversion (source, output, and
    the attempt history), for cases where .ipynb isn't wanted.
    """
    import html as html_lib

    attempts_html = ""
    for h in history:
        status = "passed" if h["valid"] else "failed"
        error_line = f"<p class='err'>{html_lib.escape(h['error'])}</p>" if h["error"] else ""
        attempts_html += f"""
        <div class="attempt {status}">
          <strong>Attempt {h['attempt']}</strong> — {status}
          {error_line}
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html_lib.escape(original_filename)} -- PySpark conversion</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
h1 {{ font-size: 20px; }}
h2 {{ font-size: 16px; margin-top: 32px; }}
pre {{ background: #f4f4f5; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; }}
.attempt {{ padding: 10px 14px; border-radius: 8px; margin-bottom: 8px; font-size: 13px; }}
.attempt.passed {{ background: #e7f8f1; }}
.attempt.failed {{ background: #fdecea; }}
.err {{ color: #b91c1c; font-family: monospace; margin: 4px 0 0; }}
.meta {{ color: #666; font-size: 13px; }}
</style></head>
<body>
<h1>PySpark conversion: {html_lib.escape(original_filename)}</h1>
<p class="meta">Generated with {html_lib.escape(model)} &middot; {attempts} attempt(s)</p>

<h2>Attempt history</h2>
{attempts_html}

<h2>Original source</h2>
<pre>{html_lib.escape(source_code)}</pre>

<h2>Converted PySpark code</h2>
<pre>{html_lib.escape(pyspark_code)}</pre>
</body></html>"""
