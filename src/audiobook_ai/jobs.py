"""Processing a reading (script, then audio), and a one-at-a-time job queue."""

import fcntl
import json
import queue
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import library, pdfview, tts
from .audio import build_audio
from .script import ModelUnavailable, QuotaError, gemini_status, write_script

# While Gemini is busy, a reading tries again after 15 minutes, then 30, then
# every hour. Each try costs a request (Google seems to count refused ones
# against the free daily limit too), hence the slowing down.
RETRY_AFTER = (15, 30, 60)       # minutes
RETRY_FOR = timedelta(hours=12)  # then it gives up and shows Retry
RETRY_CHECK_SECONDS = 30         # how often the app looks for a reading that's due
_NOT_WAITING = {"waiting_for": None, "waiting_since": None, "busy_checks": None, "retry_at": None}


def next_reset() -> datetime:
    """When the free daily limits reset (midnight Pacific time), as local time,
    plus a couple of minutes to be safe."""
    pacific = ZoneInfo("America/Los_Angeles")
    tomorrow = datetime.now(pacific).date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=pacific)
    return midnight.astimezone().replace(tzinfo=None) + timedelta(minutes=2)


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


def process(slug: str, audio_only: bool = False, log=print, on_progress=None,
            retry_when_busy: bool = False) -> dict:
    """Run a reading through the pipeline, recording progress in meta.json.

    The script step is skipped when script.md is already written, unless
    library.reset_script() was called; `audio_only` skips it regardless.
    With `retry_when_busy` (the web app), a busy Gemini leaves the reading
    waiting for an automatic retry instead of showing an error.
    """
    folder = library.reading_dir(slug)
    with _claim(folder) as mine:
        if not mine:
            log(f"{slug} is already being processed by another copy of the app; leaving it alone")
            return {**library.load_meta(slug), "skipped": True}
        return _process(slug, folder, audio_only, log, on_progress, retry_when_busy)


def _wait_for_gemini(slug: str, used_up: bool = False) -> dict:
    """Leave the reading waiting for the app to try again by itself: after the
    daily reset when the free requests are `used_up`; when Gemini is busy, in
    15 minutes, then 30, then every hour, until it has waited 12 hours, when it
    gives up and shows Retry."""
    now = datetime.now()
    if used_up:
        reset = next_reset()
        if reset - now > timedelta(hours=23):  # the reset was under an hour ago but hasn't
            reset = now + timedelta(minutes=15)  # reached this key yet: look again soon
        return library.update_meta(slug, status=library.WAITING, progress=None, error=None,
                                   **{**_NOT_WAITING, "waiting_for": "limit",
                                      "retry_at": reset.isoformat(timespec="seconds")})
    meta = library.load_meta(slug)
    since = meta.get("waiting_since") or now.isoformat(timespec="seconds")
    if now - datetime.fromisoformat(since) >= RETRY_FOR:
        hours = round(RETRY_FOR.total_seconds() / 3600)
        return library.update_meta(
            slug, status="error", progress=None, **_NOT_WAITING,
            error=f"Gemini was busy for {hours} hours, so the app stopped trying. Pages done so "
                  f"far are saved, so press Retry later.",
        )
    checks = meta.get("busy_checks") or 0
    minutes = RETRY_AFTER[min(checks, len(RETRY_AFTER) - 1)]
    return library.update_meta(slug, status=library.WAITING, progress=None, error=None,
                               waiting_for="busy", waiting_since=since, busy_checks=checks + 1,
                               retry_at=(now + timedelta(minutes=minutes)).isoformat(timespec="seconds"))


def _process(slug, folder, audio_only, log, on_progress, retry_when_busy) -> dict:
    meta = library.load_meta(slug)
    last_write = [0.0]

    def report(status: str, done: int, total: int, message: str, force: bool = False,
               eta: int | None = None) -> None:
        now = time.monotonic()
        if force or done >= total or now - last_write[0] > 0.75:
            last_write[0] = now
            progress = {"done": done, "total": total, "message": message}
            if eta is not None:
                progress["eta_seconds"] = eta
            library.update_meta(slug, status=status, error=None, progress=progress)
            if on_progress:
                on_progress(status, done, total, message)

    recording = {"t0": None, "d0": 0}

    def recording_progress(done: int, total: int) -> None:
        """Report a sentence, with a time estimate once 10 have set the pace."""
        now = time.monotonic()
        if recording["t0"] is None:
            recording["t0"], recording["d0"] = now, done
        eta = None
        if done - recording["d0"] >= 10:
            per_sentence = (now - recording["t0"]) / (done - recording["d0"])
            eta = round(per_sentence * (total - done))
        report("speaking", done, total, f"sentence {done} of {total}", eta=eta)

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
            data = build_audio(folder, meta["voice"], meta["title"], progress=recording_progress)
        if meta["kind"] == "pdf":
            report("speaking", 1, 1, "finding tables and figures", force=True)
            try:  # only for the side pane, so a failure here isn't fatal
                pdfview.info(folder, folder / meta["source"])
            except Exception:
                log(traceback.format_exc())
        return library.mark_finished(slug, status="done", progress=None, error=None,
                                     duration=data["duration"], **_NOT_WAITING)
    except QuotaError:
        if retry_when_busy:
            meta = _wait_for_gemini(slug, used_up=True)
            log(f"Gemini's free requests for today are used up. {slug} will carry on by itself "
                f"after {meta['retry_at'][11:16]}.")
            return meta
        log(traceback.format_exc())
        return library.update_meta(
            slug, status="error", progress=None, **_NOT_WAITING,
            error=f"Gemini's free requests for today are used up (they reset at "
                  f"{next_reset() - timedelta(minutes=2):%H:%M} your time). Pages done so far are "
                  f"saved, so press Retry then.",
        )
    except ModelUnavailable as exc:
        if retry_when_busy:
            meta = _wait_for_gemini(slug)
            if meta["status"] == library.WAITING:
                log(f"Gemini is busy ({str(exc)[:80]}). {slug} will try again by itself at "
                    f"{meta['retry_at'][11:16]}.")
                return meta
            log(traceback.format_exc())
            return meta
        log(traceback.format_exc())
        return library.update_meta(
            slug, status="error", progress=None,
            error="Gemini is overloaded right now (a problem on Google's side, not your limit). "
                  "Pages done so far are saved, so press Retry in a few minutes.",
        )
    except Exception as exc:  # shown in the UI; full trace in the server log
        log(traceback.format_exc())
        return library.update_meta(slug, status="error", progress=None, **_NOT_WAITING,
                                   error=f"{type(exc).__name__}: {exc}")


class Worker:
    """Processes readings one at a time in a background thread. A second
    thread puts readings that were waiting for a busy Gemini back in the queue
    once they're due and Gemini answers again."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._waiting: set[str] = set()  # in the queue
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False  # a reading is being processed right now
        self._checks = 0  # checks so far, to turn through the models

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="audiobook-worker", daemon=True)
            self._thread.start()
            threading.Thread(target=self._retry_loop, name="audiobook-retry", daemon=True).start()
        # Pick up anything left half-done when the server last stopped.
        for meta in reversed(library.list_readings()):
            if meta["status"] in library.ACTIVE:
                self.submit(meta["slug"])

    def submit(self, slug: str, audio_only: bool = False) -> None:
        with self._lock:
            if slug in self._waiting:
                return
            library.update_meta(slug, status="queued", progress=None, error=None, retry_at=None)
            self._waiting.add(slug)
        self._queue.put((slug, audio_only))

    def _retry_loop(self) -> None:
        while True:
            time.sleep(RETRY_CHECK_SECONDS)
            try:
                self._retry_due()
            except Exception:
                _log(traceback.format_exc())

    def _retry_due(self) -> None:
        """Once a waiting reading is due, ask Gemini for a one-word reply. If it
        answers, every waiting reading goes back in the queue. If it's busy,
        the readings waiting on a busy Gemini wait longer; if every model is
        used up for the day, they all wait for the reset. So one question
        covers them all. Nothing is checked while another reading is being
        made, so this never competes with it for requests."""
        if self._running or not self._queue.empty():
            return
        now = datetime.now().isoformat(timespec="seconds")
        waiting = [m for m in reversed(library.list_readings()) if m["status"] == library.WAITING]
        if not any((m.get("retry_at") or "") <= now for m in waiting):
            return
        status = gemini_status(_log, first=self._checks)
        self._checks += 1
        for slug in [m["slug"] for m in waiting]:
            try:
                with self._lock:  # leave it alone if Try now was pressed meanwhile
                    meta = library.load_meta(slug)
                    if slug in self._waiting or meta["status"] != library.WAITING:
                        continue
                    if status == "ready":
                        _log(f"Gemini is answering; carrying on with {slug}")
                        self.submit(slug)
                        continue
                    if (status == "busy" and meta.get("waiting_for") == "limit"
                            and (meta.get("retry_at") or "") > now):
                        continue  # still waits for the reset
                    meta = _wait_for_gemini(slug, used_up=status == "used up")
                if meta["status"] != library.WAITING:
                    _log(f"Gemini has been busy for hours; stopped trying {slug}")
                elif status == "used up":
                    _log(f"Gemini's free requests are used up; {slug} carries on after {meta['retry_at'][11:16]}")
                else:
                    _log(f"Gemini is still busy; {slug} will try again at {meta['retry_at'][11:16]}")
            except KeyError:  # deleted meanwhile
                pass

    def _run(self) -> None:
        while True:
            slug, audio_only = self._queue.get()
            with self._lock:
                self._waiting.discard(slug)
                self._running = True
            try:
                library.load_meta(slug)  # it may have been deleted while waiting
                process(slug, audio_only=audio_only, log=_log, retry_when_busy=True)
            except KeyError:
                pass
            except Exception:  # never let one reading stop the queue
                _log(traceback.format_exc())
            finally:
                self._running = False
                self._queue.task_done()
            if self._queue.empty():
                # Nothing else to record: let go of the voice model so the app is
                # light while you're listening or reading. It reloads when needed.
                try:
                    tts.unload()
                except Exception:
                    _log(traceback.format_exc())
