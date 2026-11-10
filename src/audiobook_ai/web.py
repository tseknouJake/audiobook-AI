"""The local web app: a library page and a read-along player."""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, library, pdfview
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


@app.middleware("http")
async def always_fresh_pages(request, call_next):
    """Make the browser check for a newer page and stylesheet on every load
    (a quick 304 when nothing changed), so app updates show up straight away."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/read/", "/static/")):
        response.headers["Cache-Control"] = "no-cache"
    return response
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
        "has_pdf": meta["kind"] == "pdf",
    }


def _pdf_or_404(slug: str):
    meta = _meta_or_404(slug)
    if meta["kind"] != "pdf":
        raise HTTPException(404, "This reading has no PDF")
    folder = library.reading_dir(slug)
    return folder, folder / meta["source"]


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
                      voice: str = Form(config.DEFAULT_VOICE), folder: str = Form("")):
    if voice not in VOICES:
        raise HTTPException(400, "Unknown voice")
    try:
        meta = library.create_reading(file.filename or "reading.pdf", await file.read(),
                                      voice=voice, title=title.strip() or None, folder=folder)
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


@app.get("/api/readings/{slug}/pdf")
def pdf_info(slug: str):
    """Page sizes, plus the tables, figures and equations found in the PDF."""
    folder, pdf = _pdf_or_404(slug)
    return pdfview.info(folder, pdf)


@app.get("/api/readings/{slug}/page/{number}.jpg")
def pdf_page(slug: str, number: int):
    folder, pdf = _pdf_or_404(slug)
    try:
        path = pdfview.page_image(folder, pdf, number)
    except IndexError:
        raise HTTPException(404, "No such page") from None
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})


@app.delete("/api/readings/{slug}")
def delete_reading(slug: str):
    """Move a reading to the Trash."""
    meta = _meta_or_404(slug)
    if meta["status"] in ("scripting", "speaking"):
        raise HTTPException(409, "It's being processed right now. Delete it once it has finished.")
    library.delete_reading(slug)
    return {"deleted": slug, "title": meta["title"]}


class Move(BaseModel):
    folder: str = ""  # "" is the top level


@app.post("/api/readings/{slug}/move")
def move_reading(slug: str, body: Move):
    _meta_or_404(slug)
    try:
        return _public(library.move_reading(slug, body.folder))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


# ---------- course folders ----------

class FolderPath(BaseModel):
    path: str


class FolderRename(BaseModel):
    path: str
    new_path: str  # a new name in the same place, or a new place


@app.get("/api/folders")
def folders():
    return {"folders": library.list_folders()}


@app.post("/api/folders")
def create_folder(body: FolderPath):
    try:
        return {"path": library.create_folder(body.path), "folders": library.list_folders()}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/folders/rename")
def rename_folder(body: FolderRename):
    try:
        return {"path": library.rename_folder(body.path, body.new_path), "folders": library.list_folders()}
    except KeyError:
        raise HTTPException(404, "No folder with that name") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/folders/delete")
def delete_folder(body: FolderPath):
    try:
        library.delete_folder(body.path)
    except KeyError:
        raise HTTPException(404, "No folder with that name") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"folders": library.list_folders()}


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
