# Spark Convert — PySpark Conversion Agent (Local, via Ollama)

A self-correcting agent that converts scripts (Python/pandas, SQL, R, etc.)
into PySpark code using a locally-running Ollama model. No API keys, no
internet calls to a hosted LLM required after setup — everything runs on
your own machine.

## What makes this an "agent" (not just a script)

It runs a **generate → validate → repair loop**, not a single one-shot call:

1. **Generate** — the model converts your script to PySpark
2. **Validate** — the output is checked with `ast.parse()` (catches broken
   Python syntax immediately, no Spark/Java installation needed)
3. **Repair** — if invalid, the broken code + the exact error is sent back
   to the model with a request to fix it, and this repeats up to
   `max_attempts` times (default 3, adjustable in the sidebar)
4. **Human-in-the-loop repair** — since real Spark execution errors (bad
   column names, logic issues) can't be caught by syntax checking alone,
   you can run the exported notebook in Databricks, paste any error back
   into the app, and the agent will do one more repair pass using that
   real error message

You can feed it files two ways — drag-and-drop in the browser, **or** drop
files straight into the `inbox\` folder while the server is running; both
paths use the identical agent loop and show up in the same browser UI.

> **Note for teams**: This app runs entirely on your own machine, calling a
> locally-installed Ollama model. There's no shared server — **each person
> who wants to use it needs to clone this repo and set up Ollama on their
> own computer** (see Installation below). Nothing here talks to the
> internet except during the one-time model download.

---

## Installation (Windows / PowerShell)

These steps assume a fresh machine. Skip any step you've already done.

### 1. Install Ollama

Download and run the installer from **https://ollama.com/download**.

### 2. (Optional but recommended) Point model storage at a drive with space

Ollama models are large (9–19GB+). If your `C:` drive is tight on space,
redirect model storage to another drive **before** pulling any models.
Open PowerShell **as Administrator**:

```powershell
[System.Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "E:\ollama\models", "Machine")
mkdir E:\ollama\models
```

Then **restart your PC** so the change fully takes effect (a simple app
restart is not reliably enough for this env var to propagate to Ollama's
background service).

After rebooting, confirm the variable is set:
```powershell
echo $env:OLLAMA_MODELS
```
It should print `E:\ollama\models`.

### 3. Pull a code-capable model

```powershell
ollama pull qwen2.5-coder:14b
```

Pick the size based on your RAM:

| Model | Approx. size | Recommended RAM |
|---|---|---|
| `qwen2.5-coder:7b` | ~4.5 GB | 8GB or less |
| `qwen2.5-coder:14b` | ~9 GB | 16GB (recommended default) |
| `qwen2.5-coder:32b` | ~19 GB | 24GB+ |

Using a model too large for your RAM causes heavy disk swapping and can
make a single conversion take 20+ minutes instead of under a minute — if
generation feels extremely slow, that's almost always the cause. Switch to
a smaller model.

Confirm the model downloaded:
```powershell
ollama list
```

### 4. Install Python

Check if you already have it:
```powershell
python --version
```

If you see an error like *"Python was not found"*, install it from
**https://www.python.org/downloads/** — on the first installer screen,
**make sure to check "Add Python to PATH"** before clicking Install. This
is the most commonly missed step. Close and reopen PowerShell afterward.

### 5. Clone this repo

```powershell
git clone <your-repo-url>
cd pyspark_agent
```

### 6. Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 7. Make sure Ollama is running

It usually starts automatically after install and stays running in the
background (system tray icon). If it's not running, open the Ollama app
once, or run:
```powershell
ollama serve
```

---

## Running it

```powershell
python webapp\app.py
```

Then open **http://localhost:5050** in your browser.

To use a specific model, port, or max repair attempts:
```powershell
python webapp\app.py --model qwen2.5-coder:14b --port 5050 --max-attempts 3
```

To stop the server, go back to its PowerShell window and press `Ctrl+C`.

---

## Using the app

There are **two ways to add a file** — both run through the same agent
loop and both show up as job cards in the browser:

- **Browser upload**: click **Add script** (top of the sidebar) to open
  the upload dialog — drag a `.py`, `.sql`, or `.r` file in, or click
  "Choose file".
- **Inbox folder**: drag a file into the `inbox\` folder in your file
  explorer while `app.py` is running. It's picked up automatically within
  a couple seconds, moved into `webapp\uploads\`, and appears in the
  browser as a job card tagged **inbox** — no need to have the browser
  tab open or focused for this to work, since watching happens on the
  server, not in JavaScript.

Once a job is running, each card shows a live **attempt track** (segmented
bar: which attempt is running, which passed/failed), a **live preview** of
code streaming in from the model, and a **stop button** to cancel
mid-generation.

- **Settings** (sidebar): see the active model, and adjust **max
  attempts** with the +/− stepper — this takes effect for the next
  conversion.
- **History** (sidebar): every file converted this session, color-coded
  by status. Click one to jump to its card.
- When a conversion finishes, the **`.ipynb`** notebook version is
  **automatically saved to `outbox\`** — no download click needed. If a
  file with that name already exists there, it's saved alongside it as
  `name (1).ipynb`, `name (2).ipynb`, etc. — nothing is ever overwritten.
- You also get three download options in the browser — clicking any of
  them saves a copy to `outbox\` too (same collision-safe renaming):
  - **`.ipynb`** — same format as the auto-saved copy
  - **`.html`** — a standalone report with the full attempt history
  - **`.py`** — the raw converted script
- **If Databricks execution fails** after importing the `.ipynb` and
  running it on serverless compute: paste the error message into the
  "Ran this in Databricks and hit an error?" box on that job's card, and
  the agent does one more repair pass using that exact error.

---

## Other ways to run it

### Command line, one-off conversion (no validation/repair loop)

```powershell
python pyspark_converter.py examples\sample_pandas.py
python pyspark_converter.py examples\sample_query.sql -o converted\output.py -m qwen2.5-coder:14b
```

### Standalone folder watcher, no browser at all (legacy)

`watch_agent.py` is a lighter-weight, browser-free version of the inbox
folder watching now built into `app.py`. Use this only if you specifically
don't want the browser UI running:

```powershell
python watch_agent.py --model qwen2.5-coder:14b
```

Note: this standalone version uses simple single-shot generation, no
validate/repair loop, no notebook export — it's simpler by design. If
`app.py` is running, its built-in inbox watcher does everything this does
plus validation, repair, and exports, so there's no need to run both.

---

## Folder structure

```
pyspark_agent/
├── inbox/       <- drag files in here (watched automatically by app.py)
├── outbox/      <- .ipynb auto-saved here on completion; .py/.html land
│                    here too if downloaded from the browser. Collision-safe
│                    renaming, never overwrites.
├── webapp/
│   ├── app.py           <- browser UI server + inbox watcher (full agent loop)
│   ├── templates/
│   ├── static/
│   ├── uploads/         <- (internal) uploaded/moved originals
│   └── converted/       <- (internal) working copies before outbox save
├── watch_agent.py       <- standalone legacy folder watcher (no validation loop)
├── agent.py             <- generate -> validate -> repair loop + notebook/html export
├── pyspark_converter.py <- shared prompt-building + CLI single-shot converter
└── examples/
```

---

## How it works

1. Reads the source script.
2. Builds a prompt with a system prompt describing PySpark conversion
   rules, pandas→PySpark / SQL→PySpark reference patterns, and a
   language-specific hint based on file extension.
3. Sends it to your local Ollama model, streaming the response token by
   token (shown live in the UI).
4. Strips markdown fences / stray commentary from the model's response.
5. Validates the result with `ast.parse()`.
6. If invalid: sends the broken code + the exact syntax error back to the
   model as a follow-up message, asking for a fix. Repeats up to
   `max_attempts` times.
7. Writes the final code, plus notebook and HTML exports, to disk.
8. If you later report a real Databricks execution error, does one more
   repair pass using that specific error message.

## Notes / limitations

- **Validation is syntax-level only** (`ast.parse()`), not execution-level.
  It catches broken Python but not logic errors, wrong column names, or
  Spark-specific runtime issues — those only surface when you actually run
  the code, which is what the Databricks human-in-the-loop step is for.
- Quality of conversion depends heavily on the model. If results are weak,
  try a larger model (RAM permitting) or add more few-shot examples to
  `BASE_SYSTEM_PROMPT` in `pyspark_converter.py` matching your codebase's
  specific patterns.
- Very large scripts may hit context-length limits depending on the model;
  consider splitting into logical chunks.
- Each repair attempt re-sends the full conversation history, so more
  attempts = more tokens = slower.
- This app runs a Flask dev server (`app.run(debug=False)`), suitable for
  local/personal use. Not intended to be exposed to the internet or run as
  a shared multi-user server as-is.

## Extending

- **Full Databricks API automation**: replace the manual paste-error step
  with a Databricks Personal Access Token + Jobs/Command Execution API
  call to run generated code automatically and feed errors back without
  a human in the loop. Needs `DATABRICKS_TOKEN` / `DATABRICKS_HOST` env
  vars and a REST call added to `agent.py`.
- **Batch mode**: loop over a directory of scripts, running the agent loop
  on each.
- **Local execution validation**: install PySpark + Java locally and swap
  the `ast.parse()` check in `agent.py` for an actual local `SparkSession`
  run against mock data — catches more than syntax errors.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ollama` command not found | Ollama isn't installed, or terminal needs restarting after install |
| `pip` not recognized | Python isn't installed, or wasn't added to PATH during install |
| Model download went to C: despite setting `OLLAMA_MODELS` | The env var needs a full PC restart (not just app restart) to take effect for Ollama's background service |
| Conversion takes 20+ minutes | Model too large for available RAM — switch to a smaller model (see size table above) |
| `[WinError 10061] ... actively refused it` | Ollama isn't running — reopen the Ollama app or run `ollama serve` |
| Page looks unstyled / old after an update | Hard refresh the browser (`Ctrl+Shift+R`) after restarting the Flask server |
| Files dropped in `inbox\` don't do anything | Make sure `python webapp\app.py` is the one running (not `watch_agent.py` or nothing) — the inbox watcher is built into `app.py` and only runs while it's active |
