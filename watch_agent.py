#!/usr/bin/env python3
"""
watch_agent.py

Drag-and-drop PySpark converter agent.

Just run this script once and leave it running. Then:
  - Drop any .py / .sql / .r script into the `inbox/` folder
  - The agent automatically detects it, converts it to PySpark
  - The converted file appears in `outbox/`
  - The original is moved to `processed/` so it doesn't get re-converted

No need to type any commands per file -- just drag and drop.

Usage:
    python watch_agent.py
    python watch_agent.py --model qwen2.5-coder:14b

Requirements:
    pip install -r requirements.txt
    ollama pull qwen2.5-coder:14b
    ollama serve   (usually auto-starts)
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pyspark_converter import convert_script  # reuse existing conversion logic

SUPPORTED_EXTENSIONS = {".py", ".sql", ".r"}

BASE_DIR = Path(__file__).parent.resolve()
INBOX = BASE_DIR / "inbox"
OUTBOX = BASE_DIR / "outbox"
PROCESSED = BASE_DIR / "processed"
FAILED = BASE_DIR / "failed"


class InboxHandler(FileSystemEventHandler):
    def __init__(self, model: str, host: str | None):
        self.model = model
        self.host = host
        # Track files currently being written to avoid double-triggering
        # on partial copies (common when dragging large files).
        self._recently_seen: set[str] = set()

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event):
        # Handles the case where a file is dragged in (often registers as a move)
        if event.is_directory:
            return
        self._handle(Path(event.dest_path))

    def _handle(self, path: Path):
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        if path.name in self._recently_seen:
            return

        self._recently_seen.add(path.name)

        # Small delay to let the OS finish writing the file (drag-and-drop
        # can trigger the event before the file is fully copied).
        time.sleep(0.5)
        if not path.exists():
            return

        self._process_file(path)
        self._recently_seen.discard(path.name)

    def _process_file(self, path: Path):
        print(f"\n[detected] {path.name} -- converting...")
        try:
            pyspark_code = convert_script(path, self.model, self.host)
        except Exception as e:
            print(f"[error] Failed to convert {path.name}: {e}")
            FAILED.mkdir(exist_ok=True)
            shutil.move(str(path), FAILED / path.name)
            print(f"[moved] {path.name} -> failed/  (see error above; "
                  f"check Ollama is running and the model is pulled)")
            return

        output_path = OUTBOX / f"{path.stem}_pyspark.py"
        output_path.write_text(pyspark_code, encoding="utf-8")
        print(f"[done] Converted -> outbox/{output_path.name}")

        PROCESSED.mkdir(exist_ok=True)
        shutil.move(str(path), PROCESSED / path.name)
        print(f"[moved] original -> processed/{path.name}")


def process_existing_files(handler: InboxHandler):
    """Convert any files already sitting in the inbox at startup."""
    for path in INBOX.iterdir():
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            handler._process_file(path)


def main():
    parser = argparse.ArgumentParser(description="Watch inbox/ and auto-convert dropped scripts to PySpark.")
    parser.add_argument("-m", "--model", type=str, default="qwen2.5-coder:14b",
                         help="Ollama model to use (default: qwen2.5-coder:14b)")
    parser.add_argument("--host", type=str, default=None,
                         help="Ollama host URL if not running on default localhost:11434")
    args = parser.parse_args()

    for folder in (INBOX, OUTBOX, PROCESSED):
        folder.mkdir(exist_ok=True)

    handler = InboxHandler(model=args.model, host=args.host)

    print("=" * 60)
    print("PySpark Conversion Agent -- watching for dropped files")
    print("=" * 60)
    print(f"  Inbox:     {INBOX}")
    print(f"  Outbox:    {OUTBOX}")
    print(f"  Processed: {PROCESSED}")
    print(f"  Model:     {args.model}")
    print("=" * 60)
    print("Drop a .py, .sql, or .r file into the inbox folder.")
    print("Press Ctrl+C to stop.\n")

    # Convert anything already waiting in the inbox
    process_existing_files(handler)

    observer = Observer()
    observer.schedule(handler, str(INBOX), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping agent...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
