"""The library: one directory per reading under library/, described by meta.json.

    library/<slug>/
        source.pdf       the upload (or source.md / source.txt)
        chunks/          Gemini's output per page range (a cache)
        script.md        the listening script; edit it and rebuild the audio
        audio.mp3        the recording, with chapters
        segments.json    start and end time of every sentence
        meta.json        title, status, progress, warnings, course folder

Course folders ("Financial Economics/Week 3") are just labels: each reading's
meta.json says which folder it's in, and library/folders.json lists the
folders (so empty ones exist too). Moving or renaming never moves files.
"""

import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from .config import LIBRARY_DIR

ACTIVE = ("queued", "scripting", "speaking")
SOURCE_TYPES = (".pdf", ".md", ".txt")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
FOLDERS_FILE = LIBRARY_DIR / "folders.json"
_lock = threading.RLock()  # meta.json and folders.json are read-modify-write


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
    with _lock:
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


def create_reading(filename: str, data: bytes, voice: str, title: str | None = None,
                   folder: str = "") -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in SOURCE_TYPES:
        raise ValueError("Upload a PDF, or a .md or .txt file.")
    if not data:
        raise ValueError("The file is empty.")
    folder = create_folder(folder) if normalize_folder(folder) else ""
    nice_name = re.sub(r"[_\s]+", " ", Path(filename).stem).strip()
    base = slugify(title or nice_name)
    slug, n = base, 2
    while (LIBRARY_DIR / slug).exists():
        slug, n = f"{base}-{n}", n + 1

    directory = LIBRARY_DIR / slug
    directory.mkdir(parents=True)
    source = directory / f"source{ext}"
    source.write_bytes(data)
    kind = "pdf" if ext == ".pdf" else "text"
    heading = None
    if kind == "text":
        text = data.decode("utf-8", errors="replace")
        (directory / "script.md").write_text(text, encoding="utf-8")
        found = re.search(r"^# (.+)$", text, re.MULTILINE)
        heading = found.group(1).strip() if found else None

    meta = {
        "slug": slug,
        "title": (title or heading or nice_name or "Untitled reading").strip(),
        "title_from_user": bool(title),
        "original_name": filename,
        "source": source.name,
        "kind": kind,
        "folder": folder,
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


def delete_reading(slug: str) -> None:
    """Move a reading's folder to the macOS Trash, so it can still be recovered."""
    from send2trash import send2trash

    folder = reading_dir(slug)
    if not (folder / "meta.json").exists():
        raise KeyError(slug)
    send2trash(str(folder))


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


# ---------- course folders ----------

def normalize_folder(path: str | None) -> str:
    """' Financial Economics / Week 3 ' -> 'Financial Economics/Week 3'; '' is the top level."""
    parts = [part.strip() for part in (path or "").split("/") if part.strip()]
    for part in parts:
        if part in (".", "..") or len(part) > 80 or re.search(r"[\x00-\x1f]", part):
            raise ValueError(f"'{part}' can't be used as a folder name.")
    return "/".join(parts)


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _inside(path: str, folder: str) -> bool:
    """True if `path` is `folder` or anywhere below it."""
    return path == folder or path.startswith(folder + "/")


def _with_ancestors(paths) -> set[str]:
    out = set()
    for path in paths:
        while path:
            out.add(path)
            path = _parent(path)
    return out


def list_folders() -> list[str]:
    """Every folder, including any a reading refers to that's missing from folders.json."""
    with _lock:
        saved = json.loads(FOLDERS_FILE.read_text(encoding="utf-8")) if FOLDERS_FILE.exists() else []
        used = [m.get("folder") or "" for m in list_readings()]
        return sorted(_with_ancestors(saved + used), key=str.lower)


def _save_folders(paths) -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FOLDERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(set(paths), key=str.lower), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FOLDERS_FILE)


def create_folder(path: str) -> str:
    """Create a folder (and any missing parents). Fine if it already exists."""
    path = normalize_folder(path)
    if not path:
        raise ValueError("Give the folder a name.")
    with _lock:
        _save_folders(set(list_folders()) | _with_ancestors([path]))
    return path


def rename_folder(path: str, new_path: str) -> str:
    """Rename or move a folder, everything inside it coming along."""
    path, new_path = normalize_folder(path), normalize_folder(new_path)
    if not path or not new_path:
        raise ValueError("Give the folder a name.")
    if new_path == path:
        return path
    if _inside(new_path, path):
        raise ValueError("A folder can't go inside itself.")
    with _lock:
        folders = list_folders()
        if path not in folders:
            raise KeyError(path)
        if new_path in folders:
            raise ValueError(f"There's already a folder called '{new_path.rsplit('/', 1)[-1]}' there.")
        moved = {new_path + f[len(path):] if _inside(f, path) else f for f in folders}
        for meta in list_readings():
            current = meta.get("folder") or ""
            if _inside(current, path):
                update_meta(meta["slug"], folder=new_path + current[len(path):])
        _save_folders(moved | _with_ancestors([new_path]))
    return new_path


def delete_folder(path: str) -> None:
    """Delete an empty folder. One with readings or folders inside is refused."""
    path = normalize_folder(path)
    with _lock:
        folders = list_folders()
        if path not in folders:
            raise KeyError(path)
        if any(f != path and _inside(f, path) for f in folders) or any(
                _inside(m.get("folder") or "", path) for m in list_readings()):
            raise ValueError("This folder isn't empty. Move or delete what's in it first.")
        _save_folders(f for f in folders if f != path)


def move_reading(slug: str, folder: str) -> dict:
    folder = normalize_folder(folder)
    if folder:
        create_folder(folder)
    return update_meta(slug, folder=folder)
