"""How many requests the app has sent to each Gemini model today.

Google's free tier allows about 20 requests a day per model and doesn't say
how many are left. It also seems to count requests it turns away as busy, so
the app keeps its own count, shown on the library page. The day starts at
midnight Pacific time, when Google resets the limits. Requests from other
apps or copies using the same key aren't counted here.
"""

import json
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config

FREE_PER_DAY = 20  # per model, as of November 2026
_lock = threading.Lock()


def _file():
    return config.LIBRARY_DIR / "gemini_usage.json"


def _day() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def today() -> dict:
    """{"date", "models": {model: {"sent", "answered", "busy", "used_up"}}}"""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get("date") != _day():
        data = {"date": _day(), "models": {}}
    return data


def record(model: str, outcome: str) -> None:
    """Count one request. `outcome` is "answered", "busy" (turned away or too
    slow) or "used up" (refused at once for the day's limit, which doesn't
    seem to count, so it isn't added to "sent")."""
    with _lock:
        data = today()
        counts = data["models"].setdefault(model, {"sent": 0, "answered": 0, "busy": 0, "used_up": False})
        if outcome == "used up":
            counts["used_up"] = True
        else:
            counts["sent"] += 1
            counts["answered" if outcome == "answered" else "busy"] += 1
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)


def used_up_today() -> set[str]:
    return {m for m, c in today()["models"].items() if c.get("used_up")}
