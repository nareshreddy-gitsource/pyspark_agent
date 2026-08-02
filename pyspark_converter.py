#!/usr/bin/env python3
"""
pyspark_converter.py

A simple local agent that converts scripts (Python/pandas, SQL, or other
tabular-processing code) into equivalent PySpark code, using a local
Ollama model.

Usage:
    python pyspark_converter.py input_script.py
    python pyspark_converter.py input_query.sql --output converted.py
    python pyspark_converter.py input_script.py --model qwen2.5-coder:14b

Requirements:
    pip install ollama
    ollama pull qwen2.5-coder:14b   (or another code-capable model)
    ollama serve                    (usually starts automatically)
"""

import argparse
import re
import sys
from pathlib import Path

import ollama


# --------------------------------------------------------------------------
# Prompt templates
# --------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """You are an expert data engineer who specializes in converting \
scripts of any kind (Python/pandas, SQL, R, shell-based data pipelines, etc.) into \
clean, idiomatic, production-quality PySpark code.

Rules you must always follow:
1. Output ONLY the final PySpark code. No explanations, no commentary, no markdown \
fences, no "Here is the code" preamble.
2. Always include necessary imports (pyspark.sql.functions as F, types, SparkSession, etc.).
3. Assume a SparkSession named `spark` already exists unless the script needs to create one.
4. Prefer PySpark DataFrame API idioms (select, withColumn, groupBy, agg, join, filter) \
over raw SQL strings, UNLESS the source is SQL, in which case preserve the query's logic \
using either spark.sql(...) or the equivalent DataFrame API -- pick whichever is more \
idiomatic for the given query.
5. Replace any row-by-row loops, pandas .apply(), or iterrows() patterns with vectorized \
Spark transformations (never simulate loops with collect() unless truly unavoidable).
6. Preserve the original logic and output columns/semantics exactly -- do not "improve" \
business logic, only translate it.
7. Add brief inline comments only where the translation isn't obvious (e.g., where a \
pandas idiom has a non-obvious Spark equivalent).
8. If the input mixes multiple languages/paradigms, convert all of it into one coherent \
PySpark script.

Here are reference patterns to follow:

# Pandas -> PySpark
pandas: df.groupby('col').agg({'x': 'sum'})
pyspark: df.groupBy('col').agg(F.sum('x').alias('x'))

pandas: df[df['col'] > 5]
pyspark: df.filter(F.col('col') > 5)

pandas: df['new'] = df['a'] + df['b']
pyspark: df = df.withColumn('new', F.col('a') + F.col('b'))

pandas: df.apply(lambda row: row['a'] * 2, axis=1)
pyspark: df = df.withColumn('a_doubled', F.col('a') * 2)

pandas: pd.merge(df1, df2, on='id', how='left')
pyspark: df1.join(df2, on='id', how='left')

pandas: df.sort_values('col', ascending=False)
pyspark: df.orderBy(F.col('col').desc())

# SQL -> PySpark
sql: SELECT a, SUM(b) FROM t GROUP BY a
pyspark: df.groupBy('a').agg(F.sum('b').alias('b'))

sql: SELECT * FROM t WHERE x > 10 AND y = 'foo'
pyspark: df.filter((F.col('x') > 10) & (F.col('y') == 'foo'))

sql: SELECT a, ROW_NUMBER() OVER (PARTITION BY a ORDER BY b DESC) as rn FROM t
pyspark: |
  from pyspark.sql.window import Window
  w = Window.partitionBy('a').orderBy(F.col('b').desc())
  df = df.withColumn('rn', F.row_number().over(w))
"""

LANGUAGE_HINTS = {
    ".py": "The source script is Python, likely using pandas and/or plain Python loops.",
    ".sql": "The source script is raw SQL. Convert it into PySpark, using spark.sql() "
    "for complex queries or the DataFrame API where it's cleaner.",
    ".r": "The source script is R (likely dplyr/data.table). Map dplyr verbs "
    "(filter, mutate, summarise, group_by, arrange, left_join) to their PySpark equivalents.",
}


def build_prompt(source_code: str, file_suffix: str) -> list[dict]:
    hint = LANGUAGE_HINTS.get(file_suffix.lower(), "The source script's language is unspecified; infer it from context.")
    system_prompt = BASE_SYSTEM_PROMPT + f"\n\nContext: {hint}"

    user_prompt = f"""Convert the following script into PySpark code.

--- SOURCE SCRIPT START ---
{source_code}
--- SOURCE SCRIPT END ---

Return only the converted PySpark code."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# --------------------------------------------------------------------------
# Output cleaning
# --------------------------------------------------------------------------

def extract_code(model_output: str) -> str:
    """
    Models often wrap code in markdown fences or add stray commentary
    despite instructions. Strip that out, keeping just the code.
    """
    text = model_output.strip()

    fence_match = re.search(r"```(?:python|pyspark)?\s*\n(.*?)```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    return text


# --------------------------------------------------------------------------
# Main conversion routine
# --------------------------------------------------------------------------

def convert_script(input_path: Path, model: str, host: str | None = None) -> str:
    source_code = input_path.read_text(encoding="utf-8")
    messages = build_prompt(source_code, input_path.suffix)

    client = ollama.Client(host=host) if host else ollama.Client()

    response = client.chat(
        model=model,
        messages=messages,
        options={
            "temperature": 0.1,  # deterministic-ish, we want faithful translation not creativity
        },
    )

    raw_output = response["message"]["content"]
    return extract_code(raw_output)


def main():
    parser = argparse.ArgumentParser(description="Convert scripts to PySpark using a local Ollama model.")
    parser.add_argument("input", type=Path, help="Path to the source script (.py, .sql, .r, etc.)")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Path to write the converted PySpark script (default: <input_stem>_pyspark.py)"
    )
    parser.add_argument(
        "-m", "--model", type=str, default="qwen2.5-coder:14b",
        help="Ollama model to use (default: qwen2.5-coder:14b)"
    )
    parser.add_argument(
        "--host", type=str, default=None,
        help="Ollama host URL if not running on default localhost:11434"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: input file '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or args.input.with_name(f"{args.input.stem}_pyspark.py")

    print(f"Converting '{args.input}' using model '{args.model}'...")
    try:
        pyspark_code = convert_script(args.input, args.model, args.host)
    except Exception as e:
        print(f"Error during conversion: {e}", file=sys.stderr)
        print(
            "Make sure Ollama is running (`ollama serve`) and the model is pulled "
            f"(`ollama pull {args.model}`).",
            file=sys.stderr,
        )
        sys.exit(1)

    output_path.write_text(pyspark_code, encoding="utf-8")
    print(f"Done. PySpark code written to '{output_path}'")


if __name__ == "__main__":
    main()
