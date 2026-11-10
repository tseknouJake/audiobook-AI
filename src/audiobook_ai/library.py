"""The library: one folder per reading under library/, described by meta.json.

    library/<slug>/
        source.pdf       the upload (or source.md / source.txt)
        chunks/          Gemini's output per page range (a cache)
        script.md        the listening script; edit it and rebuild the audio
        audio.mp3        the recording, with chapters
        segments.json    start and end time of every sentence
        meta.json        title, status, progress, warnings
"""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import LIBRARY_DIR

ACTIVE = ("queued", "scripting", "speaking")
SOURCE_TYPES = (".pdf", ".md", ".txt")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug or "reading"


def reading_dir(slug: str) -> Path:
    if not _SLUG.match(slug):
        raise KeyError(slug)
    return LIBRARY_DIR / slug


def load_meta(slug: str) -> dict:
    path = reading_dir(slug) / "meta.json"
    if not path.exists():
        raise KeyError(slug)
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(meta: dict) -> None:
    folder = reading_dir(meta["slug"])
    tmp = folder / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / "meta.json")


def update_meta(slug: str, **changes) -> dict:
    meta = load_meta(slug)
    meta.update(changes)
    save_meta(meta)
    return meta


def list_readings() -> list[dict]:
    if not LIBRARY_DIR.exists():
        return []
    metas = []
    for folder in LIBRARY_DIR.iterdir():
        if (folder / "meta.json").exists():
            try:
                metas.append(load_meta(folder.name))
            except (KeyError, json.JSONDecodeError):
                continue
    return sorted(metas, key=lambda m: m.get("created", ""), reverse=True)


def create_reading(filename: str, data: bytes, voice: str, title: str | None = None) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in SOURCE_TYPES:
        raise ValueError("Upload a PDF, or a .md or .txt file.")
    if not data:
        raise ValueError("The file is empty.")
    nice_name = re.sub(r"[_\s]+", " ", Path(filename).stem).strip()
    base = slugify(title or nice_name)
    slug, n = base, 2
    while (LIBRARY_DIR / slug).exists():
        slug, n = f"{base}-{n}", n + 1

    folder = LIBRARY_DIR / slug
    folder.mkdir(parents=True)
    source = folder / f"source{ext}"
    source.write_bytes(data)
    kind = "pdf" if ext == ".pdf" else "text"
    heading = None
    if kind == "text":
        text = data.decode("utf-8", errors="replace")
        (folder / "script.md").write_text(text, encoding="utf-8")
        found = re.search(r"^# (.+)$", text, re.MULTILINE)
        heading = found.group(1).strip() if found else None

    meta = {
        "slug": slug,
        "title": (title or heading or nice_name or "Untitled reading").strip(),
        "title_from_user": bool(title),
        "original_name": filename,
        "source": source.name,
        "kind": kind,
        "voice": voice,
        "status": "queued",
        "progress": None,
        "error": None,
        "warnings": [],
        "script_ready": kind == "text",
        "pages": None,
        "coverage": None,
        "duration": None,
        "created": _now(),
        "finished": None,
    }
    save_meta(meta)
    return meta


def reset_script(slug: str) -> None:
    """Forget Gemini's cached output so the next run writes the script afresh."""
    folder = reading_dir(slug)
    meta = load_meta(slug)
    if meta["kind"] != "pdf":
        return
    shutil.rmtree(folder / "chunks", ignore_errors=True)
    update_meta(slug, script_ready=False)


def mark_finished(slug: str, **changes) -> dict:
    return update_meta(slug, finished=_now(), **changes)
