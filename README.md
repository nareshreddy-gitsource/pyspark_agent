# PySpark Conversion Agent (Local, via Ollama)

A self-correcting agent that converts scripts (Python/pandas, SQL, R, etc.)
into PySpark code using a locally-running Ollama model. No API keys, no
internet calls to a hosted LLM required after setup.

## What makes this an "agent" (not just a script)

It runs a **generate → validate → repair loop**, not a single one-shot call:

1. **Generate** — the model converts your script to PySpark
2. **Validate** — the output is checked with `ast.parse()` (catches broken
   Python syntax immediately, no Spark/Java installation needed)
3. **Repair** — if invalid, the broken code + the exact error is sent back
   to the model with a request to fix it, and this repeats up to
   `max_attempts` times (default 3)
4. **Human-in-the-loop repair** — since real Spark execution errors (bad
   column names, logic issues) can't be caught by syntax checking alone,
   you can run the exported notebook in Databricks, paste any error back
   into the app, and the agent will do one more repair pass using that
   real error message

This means the code you get back has already passed at least a basic
correctness check, and you have a fast path to fix real runtime issues
without hand-editing anything yourself.

> **Note for teams**: This app runs entirely on your own machine, calling a
> locally-installed Ollama model. There's no shared server — **each person
> who wants to use it needs to clone this repo and set up Ollama on their
> own computer** (see Setup below). Nothing here talks to the internet
> except during the one-time model download.

## Setup

1. **Install Ollama**
   https://ollama.com/download

2. **Pull a code-capable model**
   ```bash
   ollama pull qwen2.5-coder:14b
   ```
   Alternatives depending on your hardware:
   - Lighter / less RAM (8GB or less): `qwen2.5-coder:7b`
   - Comfortable on 16GB RAM: `qwen2.5-coder:14b` (recommended default)
   - Stronger / more RAM+GPU (24GB+): `qwen2.5-coder:32b`, `deepseek-coder-v2:16b`

3. **Clone this repo**
   ```bash
   git clone <your-repo-url>
   cd pyspark_agent
   ```

4. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Make sure Ollama is running**
   It usually starts automatically after install. If not:
   ```bash
   ollama serve
   ```

## Usage — Option A: Browser UI (recommended)

Start the web app:
```bash
python webapp/app.py
```

Then open **http://localhost:5050** in your browser. Drag a script onto
the page. You'll see the agent work through it live:

- **Attempt track** — a segmented bar showing which attempt is currently
  running, and whether earlier attempts passed or failed validation
- **Live preview** — the code streaming in as the model generates it
- **Stop button** — cancel an in-progress conversion at any point

When it finishes, you get three download options:
- **`.py`** — the raw converted script
- **`.ipynb`** — a Databricks-importable notebook with the original
  source (for reference), the converted code, and a summary of how many
  attempts it took
- **`.html`** — a standalone report with the full attempt history
  (useful for sharing or keeping a record of what the agent tried)

**If Databricks execution fails** (e.g. after importing the `.ipynb` and
running it on serverless compute): paste the error message into the
"Ran this in Databricks and hit an error?" box on the job card, and the
agent will do one more repair pass using that exact error.

Different model, port, or max attempts:
```bash
python webapp/app.py --model qwen2.5-coder:32b --port 5050 --max-attempts 5
```

To stop it, go to its terminal window and press `Ctrl+C`.

## Usage — Option B: Drag-and-drop folder watcher (no browser, single-shot)

Note: the folder watcher (`watch_agent.py`) still uses the simpler
single-shot generation, not the full agent loop. Use the browser UI
(Option A) if you want validation and repair.

Start the watcher once:
```bash
python watch_agent.py
```

Leave that terminal window running in the background. Then, anytime you
want to convert a file, just **drag it into the `inbox/` folder** (in your
file explorer / Finder, no terminal needed). Within a couple seconds:

- The converted PySpark code appears in `outbox/<name>_pyspark.py`
- The original file moves to `processed/` (so it won't be re-converted)
- If conversion fails (e.g. Ollama isn't running), the file moves to
  `failed/` and the error is printed in the terminal window

Supported drop-in file types: `.py`, `.sql`, `.r`

To use a different model:
```bash
python watch_agent.py --model deepseek-coder-v2:16b
```

**Running either option automatically on startup** (optional):
- **Windows**: create a shortcut (or a small `.bat` file) in your Startup
  folder (`shell:startup` in the Run dialog).
- **Mac**: add it as a Login Item, or use `launchd`.
- **Linux**: add a `systemd --user` service or an autostart `.desktop` entry.

## Usage — Option C: Command line (one-off conversions)

```bash
python pyspark_converter.py examples/sample_pandas.py
python pyspark_converter.py examples/sample_query.sql
```

Custom output path:
```bash
python pyspark_converter.py examples/sample_pandas.py -o converted/output.py
```

Different model:
```bash
python pyspark_converter.py examples/sample_pandas.py -m deepseek-coder-v2:16b
```

Remote Ollama host:
```bash
python pyspark_converter.py examples/sample_pandas.py --host http://192.168.1.10:11434
```

## Folder structure (created automatically on first run)

```
pyspark_agent/
├── inbox/       <- (watch_agent) drag files in here
├── outbox/      <- (watch_agent) converted files appear here
├── processed/   <- (watch_agent) originals moved here after success
├── failed/      <- (watch_agent) originals moved here on error
├── webapp/
│   ├── app.py           <- browser UI server (full agent loop)
│   ├── templates/
│   ├── static/
│   ├── uploads/         <- (web UI) uploaded originals
│   └── converted/       <- (web UI) converted .py / .ipynb / .html output
├── watch_agent.py       <- single-shot folder watcher (no validation loop)
├── agent.py             <- the generate -> validate -> repair loop + notebook/html export
├── pyspark_converter.py <- shared prompt-building + CLI single-shot converter
└── examples/
```

## How it works

1. Reads the source script.
2. Builds a prompt with:
   - A system prompt describing PySpark conversion rules
   - A handful of pandas→PySpark and SQL→PySpark reference patterns (few-shot examples)
   - A language-specific hint based on the file extension (.py, .sql, .r)
3. Sends it to your local Ollama model, streaming the response token by token.
4. Strips markdown fences / stray commentary from the model's response.
5. Validates the result with `ast.parse()`.
6. If invalid: sends the broken code + the exact syntax error back to the
   model as a follow-up message in the same conversation, asking for a fix.
   Repeats up to `max_attempts` times.
7. Writes the final code, plus notebook and HTML exports, to disk.
8. If you later report a real Databricks execution error, does one more
   repair pass using that specific error message.

## Notes / limitations

- **Validation is syntax-level only** (`ast.parse()`), not execution-level.
  It catches broken Python (missing parens, bad indentation, undefined
  syntax) but not logic errors, wrong column names, or Spark-specific
  runtime issues — those only show up when you actually run the code
  against real data, which is what the Databricks human-in-the-loop step
  is for.
- Quality of conversion depends heavily on the model. If you're getting
  weak results, try a larger model (14b → 32b, RAM permitting) or add more
  few-shot examples to `BASE_SYSTEM_PROMPT` in `pyspark_converter.py` that
  match your specific codebase's patterns.
- For very large scripts, you may hit context-length limits depending on
  the model. Consider splitting large files into logical chunks (e.g., by
  function) and converting separately.
- Each repair attempt re-sends the full conversation history to the model,
  so more attempts = more tokens = slower. `max_attempts=3` (default) is
  usually a reasonable ceiling.

## Extending

Ideas if you want to build this out further:
- **Full automation with Databricks API**: instead of manually pasting
  errors back, use a Databricks Personal Access Token + the Jobs/Command
  Execution API to actually run the generated code on serverless compute
  and feed errors back automatically — turning the human-in-the-loop step
  into a fully automated one. Ask if you want this built; it just needs a
  `DATABRICKS_TOKEN` and `DATABRICKS_HOST` environment variable and a
  REST call added to `agent.py`.
- **Batch mode**: loop over a directory of scripts and convert them all,
  running the agent loop on each.
- **Local execution validation**: if you install PySpark + Java locally,
  swap the `ast.parse()` check in `agent.py` for an actual `spark-submit`
  or local `SparkSession` run against small mock data — catches more than
  syntax errors, but needs local infrastructure.
