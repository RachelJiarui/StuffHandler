"""Background processing for uploaded photos.

A tiny in-process job runner: a queue plus a few daemon worker threads,
started lazily on the first job. Job state lives in the Mongo `uploads`
collection so status polling works from any web process; the threads
themselves run in whichever process accepted the upload/redo request.

Heavy resources are created once, on first use: the rembg session (slow
import + model load) and the OpenAI client. Their imports are deferred so
the website still starts instantly.
"""

import os
import queue
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

STALE_ERROR = "Interrupted by a server restart — redo to retry."


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_stale_jobs(uploads) -> None:
    """Jobs live in worker threads, so a restart silently drops any that
    were mid-flight — surface them as failed instead of spinning forever."""
    uploads.update_many(
        {"status": "processing"},
        {
            "$set": {"status": "failed", "error": STALE_ERROR, "updated_at": now_iso()},
            "$unset": {"prev": "", "run_token": ""},
        },
    )


class UploadProcessor:
    def __init__(self, uploads, originals_dir: Path, processed_dir: Path, workers: int = 3):
        self.uploads = uploads
        self.originals_dir = originals_dir
        self.processed_dir = processed_dir
        # Each concurrent job can hold its own onnxruntime session in
        # memory at once — on a memory-constrained deployment this should
        # be dropped to 1 (serialize processing) rather than left at the
        # dev-machine default.
        self.workers = max(1, workers)
        self._queue: queue.Queue = queue.Queue()
        self._start_lock = threading.Lock()
        self._started = False
        self._rembg_lock = threading.Lock()
        self._rembg_session = None
        self._openai_lock = threading.Lock()
        self._openai_client = None

    # -- lazy heavy resources ----------------------------------------------

    def _rembg(self):
        with self._rembg_lock:
            if self._rembg_session is None:
                from rembg import new_session
                self._rembg_session = new_session("isnet-general-use")
            return self._rembg_session

    def _openai(self):
        with self._openai_lock:
            if self._openai_client is None:
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise RuntimeError(
                        "OPENAI_API_KEY is not set — add it to .env to use AI clean-up"
                    )
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=api_key)
            return self._openai_client

    # -- job runner ----------------------------------------------------------

    def enqueue(self, upload_id: str) -> None:
        with self._start_lock:
            if not self._started:
                for _ in range(self.workers):
                    threading.Thread(target=self._worker, daemon=True).start()
                self._started = True
        self._queue.put(upload_id)

    def _worker(self) -> None:
        while True:
            upload_id = self._queue.get()
            try:
                self._run(upload_id)
            except Exception:  # never let a bad photo kill the worker
                pass

    def _run(self, upload_id: str) -> None:
        doc = self.uploads.find_one({"_id": upload_id})
        if not doc or doc.get("status") != "processing":
            return  # deleted or stopped while queued
        # The run token ties this job to the doc state that enqueued it: if
        # the run is stopped (or superseded) while we work, the token no
        # longer matches and the result is discarded instead of clobbering
        # the reverted doc / previous processed file.
        token = doc.get("run_token")
        try:
            png = self._process(doc)
        except Exception as e:
            self._finish(upload_id, token, error=str(e) or type(e).__name__)
            return
        tmp = self.processed_dir / f"{upload_id}.{token}.tmp"
        tmp.write_bytes(png)
        if self._finish(upload_id, token, error=None):
            os.replace(tmp, self.processed_dir / f"{upload_id}.png")
        else:
            tmp.unlink(missing_ok=True)

    def _finish(self, upload_id: str, token, error: str | None) -> bool:
        """Complete the run — but only if it wasn't stopped/superseded
        meanwhile. Returns whether this run still owned the doc."""
        result = self.uploads.update_one(
            {"_id": upload_id, "status": "processing", "run_token": token},
            {
                "$set": {
                    "status": "failed" if error else "done",
                    "error": error,
                    "updated_at": now_iso(),
                },
                "$unset": {"prev": "", "run_token": ""},
            },
        )
        return result.modified_count == 1

    # -- the actual pipeline ---------------------------------------------------

    def _process(self, doc: dict) -> bytes:
        if doc["source"] == "processed":
            src = self.processed_dir / f"{doc['_id']}.png"
        else:
            src = self.originals_dir / doc["original_file"]
        # Read fully into memory: when redoing from "processed" the output
        # overwrites this same file, so it can't stay lazily open.
        img = Image.open(BytesIO(src.read_bytes()))

        if doc["enhance"]:
            from pipeline.enhance import DEFAULT_PROMPT, enhance_image
            prompt = DEFAULT_PROMPT
            message = (doc.get("message") or "").strip()
            if message:
                prompt += " Additional guidance from the owner: " + message
            img = enhance_image(self._openai(), img, prompt)

        if doc["remove_bg"]:
            from pipeline.remove_bg import remove_background
            img = remove_background(img, self._rembg())

        buf = BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
