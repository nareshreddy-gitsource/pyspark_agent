#!/usr/bin/env python3
"""
app.py

Browser-based UI for the PySpark conversion agent. Runs the full
generate -> validate -> repair loop (see agent.py) and can export
results as a Databricks-importable .ipynb notebook or a standalone
HTML report.

Usage:
    python app.py
    python app.py --model qwen2.5-coder:14b --max-attempts 3

Then open http://localhost:5050 in your browser.

Requirements:
    pip install -r requirements.txt
    ollama pull qwen2.5-coder:14b
    ollama serve   (usually auto-starts)
"""

import argparse
import json
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, render_template
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

sys.path.insert(0, str(Path(__file__).parent.parent))
from agent import run_agent, repair_with_external_error, build_notebook, build_html_report

BASE_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE_DIR / "uploads"
CONVERTED_DIR = BASE_DIR / "converted"
UPLOAD_DIR.mkdir(exist_ok=True)
CONVERTED_DIR.mkdir(exist_ok=True)

INBOX_DIR = BASE_DIR.parent / "inbox"
OUTBOX_DIR = BASE_DIR.parent / "outbox"
INBOX_DIR.mkdir(exist_ok=True)
OUTBOX_DIR.mkdir(exist_ok=True)

SUPPORTED_EXTENSIONS = {".py", ".sql", ".r"}

app = Flask(__name__)

# In-memory job tracking.
# status progression: queued -> generating -> validating -> done | needs_repair | failed | stopped
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
CANCEL_FLAGS: dict[str, threading.Event] = {}

MODEL_NAME = "qwen2.5-coder:14b"
OLLAMA_HOST = None
MAX_ATTEMPTS = 3


def save_to_outbox(content: str, filename: str) -> Path:
    """
    Writes content to outbox/<filename>, auto-renaming on collision as
    "name (1).ext", "name (2).ext", etc. -- never overwrites, never uses
    version-number suffixes like _v1/_v2.
    """
    target = OUTBOX_DIR / filename
    if not target.exists():
        target.write_text(content, encoding="utf-8")
        return target

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate = OUTBOX_DIR / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            candidate.write_text(content, encoding="utf-8")
            return candidate
        counter += 1


def update_job(job_id: str, **fields):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def run_conversion_job(job_id: str, saved_path: Path, original_name: str):
    cancel_event = CANCEL_FLAGS[job_id]

    try:
        source_code = saved_path.read_text(encoding="utf-8")
    except Exception as e:
        update_job(job_id, status="failed", stage_detail=f"Could not read file: {e}")
        return

    update_job(job_id, source_code=source_code, status="generating", stage_detail="Generating…", attempt=1, max_attempts=MAX_ATTEMPTS)

    def on_event(evt):
        if cancel_event.is_set():
            raise _Cancelled()

        evt_type = evt["type"]
        if evt_type == "attempt_start":
            update_job(job_id, status="generating", attempt=evt["attempt"], max_attempts=evt["max_attempts"],
                       stage_detail=f"Generating (attempt {evt['attempt']}/{evt['max_attempts']})", live_preview="")
        elif evt_type == "token":
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    prev = job.get("live_preview", "")
                    job["live_preview"] = (prev + evt["chunk"])[-2000:]
        elif evt_type == "validating":
            update_job(job_id, status="validating", stage_detail=f"Checking attempt {evt['attempt']}…")
        elif evt_type == "attempt_failed":
            update_job(job_id, stage_detail=f"Attempt {evt['attempt']} failed: {evt['error']} — retrying…")
        elif evt_type == "attempt_passed":
            update_job(job_id, stage_detail=f"Attempt {evt['attempt']} passed validation")
        elif evt_type == "done":
            pass  # handled after run_agent returns
        elif evt_type == "exhausted":
            pass  # handled after run_agent returns

    try:
        result = run_agent(
            source_code=source_code,
            file_suffix=saved_path.suffix,
            model=MODEL_NAME,
            host=OLLAMA_HOST,
            max_attempts=MAX_ATTEMPTS,
            on_event=on_event,
        )
    except _Cancelled:
        update_job(job_id, status="stopped", stage_detail="Stopped by user")
        CANCEL_FLAGS.pop(job_id, None)
        return
    except Exception as e:
        update_job(job_id, status="failed", stage_detail=str(e), error=str(e))
        CANCEL_FLAGS.pop(job_id, None)
        return

    stem = Path(original_name).stem
    code_path = CONVERTED_DIR / f"{job_id}_{stem}_pyspark.py"
    code_path.write_text(result["code"], encoding="utf-8")

    if result["success"]:
        notebook = build_notebook(
            original_filename=original_name,
            source_code=source_code,
            pyspark_code=result["code"],
            model=MODEL_NAME,
            attempts=result["attempts"],
        )
        notebook_json = json.dumps(notebook, indent=1)
        outbox_path = save_to_outbox(notebook_json, f"{stem}_pyspark.ipynb")
        update_job(
            job_id,
            status="done",
            stage_detail=f"Passed validation on attempt {result['attempts']}/{MAX_ATTEMPTS} — saved to outbox/{outbox_path.name}",
            code=result["code"],
            attempts_used=result["attempts"],
            history=result["history"],
            output_code_filename=code_path.name,
            preview=result["code"][:4000],
        )
    else:
        # Still hand back the best-effort code -- syntax check failed after
        # all retries, but the file is still there for manual inspection/export.
        update_job(
            job_id,
            status="needs_repair",
            stage_detail=f"Still invalid after {MAX_ATTEMPTS} attempts: {result['last_error']}",
            code=result["code"],
            attempts_used=result["attempts"],
            history=result["history"],
            output_code_filename=code_path.name,
            preview=result["code"][:4000],
            last_error=result["last_error"],
        )

    CANCEL_FLAGS.pop(job_id, None)


class _Cancelled(Exception):
    pass


def create_job(source_label: str, filename: str) -> str:
    """
    Registers a new job and kicks off its conversion thread. Used by both
    the browser upload route and the inbox folder watcher, so both paths
    behave identically and show up in the same UI.
    """
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "filename": filename,
            "source": source_label,  # "upload" or "inbox"
            "status": "queued",
            "stage_detail": "Waiting in queue",
            "attempt": 0,
            "max_attempts": MAX_ATTEMPTS,
            "live_preview": "",
            "submitted_at": time.time(),
        }
    CANCEL_FLAGS[job_id] = threading.Event()
    return job_id


def start_conversion_thread(job_id: str, saved_path: Path, original_name: str):
    thread = threading.Thread(target=run_conversion_job, args=(job_id, saved_path, original_name), daemon=True)
    thread.start()


# --------------------------------------------------------------------------
# Inbox folder watcher -- lets people drag files into inbox/ as an
# alternative to using the browser's Add script modal. Runs the exact same
# agent loop and shows up as a normal job card in the UI.
# --------------------------------------------------------------------------

class InboxHandler(FileSystemEventHandler):
    def __init__(self):
        self._recently_seen: set[str] = set()

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.dest_path))

    def _handle(self, path: Path):
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        if path.name in self._recently_seen:
            return
        self._recently_seen.add(path.name)

        # Let the OS finish writing the file before reading it (drag-and-drop
        # can fire the event before the copy completes).
        time.sleep(0.4)
        if not path.exists():
            self._recently_seen.discard(path.name)
            return

        job_id = create_job(source_label="inbox", filename=path.name)
        saved_path = UPLOAD_DIR / f"{job_id}_{path.name}"
        try:
            shutil.move(str(path), saved_path)
        except Exception:
            self._recently_seen.discard(path.name)
            return

        start_conversion_thread(job_id, saved_path, path.name)
        self._recently_seen.discard(path.name)


def process_existing_inbox_files(handler: InboxHandler):
    """Pick up any files already sitting in inbox/ at server startup."""
    for path in INBOX_DIR.iterdir():
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            handler._handle(path)


def start_inbox_watcher():
    handler = InboxHandler()
    process_existing_inbox_files(handler)
    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=False)
    observer.daemon = True
    observer.start()
    return observer


@app.route("/settings", methods=["GET", "POST"])
def settings():
    global MAX_ATTEMPTS
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        new_max = data.get("max_attempts")
        if isinstance(new_max, int) and 1 <= new_max <= 10:
            MAX_ATTEMPTS = new_max
        return jsonify({"ok": True, "model": MODEL_NAME, "max_attempts": MAX_ATTEMPTS})
    return jsonify({"model": MODEL_NAME, "max_attempts": MAX_ATTEMPTS})


@app.route("/")
def index():
    return render_template("index.html", model_name=MODEL_NAME, max_attempts=MAX_ATTEMPTS)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type '{suffix}'. Use .py, .sql, or .r"}), 400

    job_id = create_job(source_label="upload", filename=file.filename)
    saved_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    file.save(saved_path)

    start_conversion_thread(job_id, saved_path, file.filename)

    return jsonify({"job_id": job_id})


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    event = CANCEL_FLAGS.get(job_id)
    if not event:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job and job.get("status") in ("done", "failed", "stopped", "needs_repair"):
            return jsonify({"ok": True, "note": "Already finished"})
        return jsonify({"error": "Unknown or already-finished job"}), 404
    event.set()
    return jsonify({"ok": True})


@app.route("/status/<job_id>")
def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    # Don't ship the full source/code blobs on every poll -- trim for status pings
    slim = {k: v for k, v in job.items() if k not in ("source_code",)}
    return jsonify(slim)


@app.route("/jobs")
def all_jobs():
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: j["submitted_at"], reverse=True)
    return jsonify(jobs)


@app.route("/download/<job_id>")
def download(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("output_code_filename"):
        return jsonify({"error": "File not ready"}), 404
    stem = Path(job["filename"]).stem
    download_name = f"{stem}_pyspark.py"

    save_to_outbox(job.get("code", ""), download_name)

    return send_from_directory(
        CONVERTED_DIR,
        job["output_code_filename"],
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/download_notebook/<job_id>")
def download_notebook(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "code" not in job:
        return jsonify({"error": "Not ready"}), 404

    notebook = build_notebook(
        original_filename=job["filename"],
        source_code=job.get("source_code", ""),
        pyspark_code=job["code"],
        model=MODEL_NAME,
        attempts=job.get("attempts_used", 1),
    )
    stem = Path(job["filename"]).stem
    nb_path = CONVERTED_DIR / f"{job_id}_{stem}.ipynb"
    notebook_json = json.dumps(notebook, indent=1)
    nb_path.write_text(notebook_json, encoding="utf-8")

    download_name = f"{stem}_pyspark.ipynb"
    save_to_outbox(notebook_json, download_name)

    return send_from_directory(CONVERTED_DIR, nb_path.name, as_attachment=True, download_name=download_name)


@app.route("/download_html/<job_id>")
def download_html(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "code" not in job:
        return jsonify({"error": "Not ready"}), 404

    html_report = build_html_report(
        original_filename=job["filename"],
        source_code=job.get("source_code", ""),
        pyspark_code=job["code"],
        model=MODEL_NAME,
        attempts=job.get("attempts_used", 1),
        history=job.get("history", []),
    )
    stem = Path(job["filename"]).stem
    html_path = CONVERTED_DIR / f"{job_id}_{stem}.html"
    html_path.write_text(html_report, encoding="utf-8")

    download_name = f"{stem}_pyspark.html"
    save_to_outbox(html_report, download_name)

    return send_from_directory(CONVERTED_DIR, html_path.name, as_attachment=True, download_name=download_name)


@app.route("/report_error/<job_id>", methods=["POST"])
def report_error(job_id):
    """
    Human-in-the-loop repair: the user ran the notebook in Databricks,
    hit a real runtime error, and pastes it back here. We send the
    broken code + that exact error back to the model for one repair pass.
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "code" not in job:
        return jsonify({"error": "Job not found or not ready"}), 404

    data = request.get_json(force=True) or {}
    external_error = data.get("error", "").strip()
    if not external_error:
        return jsonify({"error": "No error text provided"}), 400

    update_job(job_id, status="generating", stage_detail="Repairing with reported error…", attempt=1, max_attempts=1, live_preview="")

    def on_event(evt):
        evt_type = evt["type"]
        if evt_type == "token":
            with JOBS_LOCK:
                j = JOBS.get(job_id)
                if j is not None:
                    prev = j.get("live_preview", "")
                    j["live_preview"] = (prev + evt["chunk"])[-2000:]
        elif evt_type == "validating":
            update_job(job_id, status="validating", stage_detail="Checking repaired code…")

    result = repair_with_external_error(
        source_code=job.get("source_code", ""),
        file_suffix=Path(job["filename"]).suffix,
        broken_code=job["code"],
        external_error=external_error,
        model=MODEL_NAME,
        host=OLLAMA_HOST,
        on_event=on_event,
    )

    stem = Path(job["filename"]).stem
    code_path = CONVERTED_DIR / f"{job_id}_{stem}_pyspark.py"
    code_path.write_text(result["code"], encoding="utf-8")

    status_val = "done" if result["success"] else "needs_repair"
    stage_detail = "Repaired and re-validated" if result["success"] else f"Still invalid: {result['last_error']}"

    if result["success"]:
        notebook = build_notebook(
            original_filename=job["filename"],
            source_code=job.get("source_code", ""),
            pyspark_code=result["code"],
            model=MODEL_NAME,
            attempts=job.get("attempts_used", 1) + 1,
        )
        outbox_path = save_to_outbox(json.dumps(notebook, indent=1), f"{stem}_pyspark.ipynb")
        stage_detail += f" — saved to outbox/{outbox_path.name}"

    update_job(
        job_id,
        status=status_val,
        stage_detail=stage_detail,
        code=result["code"],
        attempts_used=job.get("attempts_used", 1) + 1,
        preview=result["code"][:4000],
        last_error=result["last_error"],
    )

    return jsonify({"ok": True, "success": result["success"]})


def main():
    global MODEL_NAME, OLLAMA_HOST, MAX_ATTEMPTS

    parser = argparse.ArgumentParser(description="Web UI for the self-correcting PySpark conversion agent.")
    parser.add_argument("-m", "--model", type=str, default="qwen2.5-coder:14b",
                         help="Ollama model to use (default: qwen2.5-coder:14b)")
    parser.add_argument("--host", type=str, default=None,
                         help="Ollama host URL if not running on default localhost:11434")
    parser.add_argument("--port", type=int, default=5050, help="Port to serve the UI on (default: 5050)")
    parser.add_argument("--max-attempts", type=int, default=3,
                         help="Max generate-validate-repair attempts per file (default: 3)")
    args = parser.parse_args()

    MODEL_NAME = args.model
    OLLAMA_HOST = args.host
    MAX_ATTEMPTS = args.max_attempts

    print("=" * 60)
    print("PySpark Conversion Agent -- Web UI")
    print("=" * 60)
    print(f"  Model:        {MODEL_NAME}")
    print(f"  Max attempts: {MAX_ATTEMPTS}")
    print(f"  Open:         http://localhost:{args.port}")
    print(f"  Watching:     {INBOX_DIR}  (drop .py/.sql/.r files here too)")
    print("=" * 60)

    start_inbox_watcher()

    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
