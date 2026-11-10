"""Processing a reading (script, then audio), and a one-at-a-time job queue."""

import fcntl
import json
import queue
import sys
import threading
import time
import traceback
from contextlib import contextmanager

from . import library, pdfview
from .audio import build_audio
from .script import ModelUnavailable, QuotaError, write_script


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@contextmanager
def _claim(folder):
    """Hold a lock on the reading while working on it, so a second copy of the
    app (another `audiobook serve`, or the CLI) leaves it alone."""
    handle = open(folder / ".processing.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def process(slug: str, audio_only: bool = False, log=print, on_progress=None) -> dict:
    """Run a reading through the pipeline, recording progress in meta.json.

    The script step is skipped when script.md is already written, unless
    library.reset_script() was called; `audio_only` skips it regardless.
    """
    folder = library.reading_dir(slug)
    with _claim(folder) as mine:
        if not mine:
            log(f"{slug} is already being processed by another copy of the app; leaving it alone")
            return {**library.load_meta(slug), "skipped": True}
        return _process(slug, folder, audio_only, log, on_progress)


def _process(slug, folder, audio_only, log, on_progress) -> dict:
    meta = library.load_meta(slug)
    last_write = [0.0]

    def report(status: str, done: int, total: int, message: str, force: bool = False) -> None:
        now = time.monotonic()
        if force or done >= total or now - last_write[0] > 0.75:
            last_write[0] = now
            library.update_meta(slug, status=status, error=None,
                                progress={"done": done, "total": total, "message": message})
            if on_progress:
                on_progress(status, done, total, message)

    script_path = folder / "script.md"
    unchanged = False
    try:
        if not audio_only and not meta.get("script_ready"):
            before = script_path.read_text(encoding="utf-8") if script_path.exists() else None
            report("scripting", 0, 1, "sending pages to Gemini", force=True)
            info = write_script(
                folder / meta["source"], folder,
                progress=lambda done, total, msg: report("scripting", done, total, msg),
                log=log,
            )
            changes = {key: info[key] for key in ("pages", "coverage", "warnings", "model")}
            changes["script_ready"] = True
            if info["script_title"] and not meta.get("title_from_user"):
                changes["title"] = info["script_title"]
            meta = library.update_meta(slug, **changes)
            unchanged = before == script_path.read_text(encoding="utf-8")

        timing = folder / "segments.json"
        recorded = json.loads(timing.read_text(encoding="utf-8")) if timing.exists() else {}
        if unchanged and (folder / "audio.mp3").exists() and recorded.get("voice") == meta["voice"]:
            data = recorded  # a re-check that changed nothing: keep the existing recording
        else:
            report("speaking", 0, 1, "loading the voice", force=True)
            data = build_audio(
                folder, meta["voice"], meta["title"],
                progress=lambda done, total: report("speaking", done, total, f"sentence {done} of {total}"),
            )
        if meta["kind"] == "pdf":
            report("speaking", 1, 1, "finding tables and figures", force=True)
            try:  # only for the side pane, so a failure here isn't fatal
                pdfview.info(folder, folder / meta["source"])
            except Exception:
                log(traceback.format_exc())
        return library.mark_finished(slug, status="done", progress=None, error=None,
                                     duration=data["duration"])
    except QuotaError:
        log(traceback.format_exc())
        return library.update_meta(
            slug, status="error", progress=None,
            error="Gemini's free limit is used up for now. Pages done so far are saved, "
                  "so press Retry later (or set GEMINI_FALLBACK_MODEL in .env).",
        )
    except ModelUnavailable:
        log(traceback.format_exc())
        return library.update_meta(
            slug, status="error", progress=None,
            error="Gemini is overloaded right now (a problem on Google's side, not your limit). "
                  "Pages done so far are saved, so press Retry in a few minutes.",
        )
    except Exception as exc:  # shown in the UI; full trace in the server log
        log(traceback.format_exc())
        return library.update_meta(slug, status="error", progress=None,
                                   error=f"{type(exc).__name__}: {exc}")


class Worker:
    """Processes readings one at a time in a background thread."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._waiting: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="audiobook-worker", daemon=True)
            self._thread.start()
        # Pick up anything left half-done when the server last stopped.
        for meta in reversed(library.list_readings()):
            if meta["status"] in library.ACTIVE:
                self.submit(meta["slug"])

    def submit(self, slug: str, audio_only: bool = False) -> None:
        with self._lock:
            if slug in self._waiting:
                return
            self._waiting.add(slug)
        library.update_meta(slug, status="queued", progress=None, error=None)
        self._queue.put((slug, audio_only))

    def _run(self) -> None:
        while True:
            slug, audio_only = self._queue.get()
            with self._lock:
                self._waiting.discard(slug)
            try:
                library.load_meta(slug)  # it may have been deleted while waiting
                process(slug, audio_only=audio_only, log=_log)
            except KeyError:
                pass
            except Exception:  # never let one reading stop the queue
                _log(traceback.format_exc())
            finally:
                self._queue.task_done()
