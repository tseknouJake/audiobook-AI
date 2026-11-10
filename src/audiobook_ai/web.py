"""The local web app: a library page and a read-along player."""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, library
from .jobs import Worker
from .voices import VOICES, sample_path

worker = Worker()
config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
config.VOICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    worker.start()
    yield


app = FastAPI(title="Audiobook AI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.mount("/files", StaticFiles(directory=config.LIBRARY_DIR), name="files")
app.mount("/samples", StaticFiles(directory=config.VOICE_SAMPLES_DIR), name="samples")


def _meta_or_404(slug: str) -> dict:
    try:
        return library.load_meta(slug)
    except KeyError:
        raise HTTPException(404, "No reading with that name") from None


def _public(meta: dict) -> dict:
    slug = meta["slug"]
    folder = library.reading_dir(slug)
    has_audio = (folder / "audio.mp3").exists()
    version = (meta.get("finished") or "").replace(":", "")
    return {
        **meta,
        "voice_label": VOICES.get(meta["voice"], meta["voice"]),
        "audio_url": f"/files/{slug}/audio.mp3?v={version}" if has_audio else None,
        "script_path": str(folder / "script.md"),
    }


@app.get("/", include_in_schema=False)
def index_page():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/read/{slug}", include_in_schema=False)
def reader_page(slug: str):
    _meta_or_404(slug)
    return FileResponse(config.STATIC_DIR / "reader.html")


@app.get("/api/status")
def status():
    return {
        "gemini_key": config.has_gemini_key(),
        "gemini_model": config.GEMINI_MODEL,
        "default_voice": config.DEFAULT_VOICE,
    }


@app.get("/api/voices")
def voices():
    return [
        {"id": vid, "label": label,
         "sample": f"/samples/{vid}.mp3" if sample_path(vid).exists() else None}
        for vid, label in VOICES.items()
    ]


@app.get("/api/readings")
def readings():
    return [_public(meta) for meta in library.list_readings()]


@app.post("/api/readings")
async def add_reading(file: UploadFile = File(...), title: str = Form(""),
                      voice: str = Form(config.DEFAULT_VOICE)):
    if voice not in VOICES:
        raise HTTPException(400, "Unknown voice")
    try:
        meta = library.create_reading(file.filename or "reading.pdf", await file.read(),
                                      voice=voice, title=title.strip() or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    worker.submit(meta["slug"])
    return _public(library.load_meta(meta["slug"]))


@app.get("/api/readings/{slug}")
def reading(slug: str):
    meta = _meta_or_404(slug)
    seg_path = library.reading_dir(slug) / "segments.json"
    timing = json.loads(seg_path.read_text(encoding="utf-8")) if seg_path.exists() else None
    return {**_public(meta), "timing": timing}


class Rebuild(BaseModel):
    voice: str | None = None
    script: bool = False  # ask Gemini for a fresh script (spends free-tier requests)


@app.post("/api/readings/{slug}/rebuild")
def rebuild(slug: str, body: Rebuild):
    """Retry after an error, re-record after editing script.md, or switch voice."""
    meta = _meta_or_404(slug)
    if meta["status"] in library.ACTIVE:
        raise HTTPException(409, "This reading is already being processed")
    if body.voice:
        if body.voice not in VOICES:
            raise HTTPException(400, "Unknown voice")
        library.update_meta(slug, voice=body.voice)
    if body.script:
        library.reset_script(slug)
    worker.submit(slug)
    return _public(library.load_meta(slug))
