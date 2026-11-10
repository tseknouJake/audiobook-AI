"""Command line: `audiobook serve`, `audiobook add paper.pdf`, and friends."""

import argparse
import sys
from pathlib import Path

from . import config


def _serve(args) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://127.0.0.1:{args.port}"
    print(f"Audiobook AI running at {url}  (Ctrl+C to stop)")
    if args.open:
        threading.Timer(1.5, webbrowser.open, (url,)).start()
    uvicorn.run("audiobook_ai.web:app", host="127.0.0.1", port=args.port, log_level="warning")


def _print_progress(status, done, total, message) -> None:
    label = {"scripting": "Writing script", "speaking": "Recording"}.get(status, status)
    print(f"\r  {label}: {message}".ljust(70), end="", flush=True)


def _run(slug: str, audio_only: bool = False) -> int:
    from .audio import format_duration
    from .jobs import process

    meta = process(slug, audio_only=audio_only, log=lambda msg: None, on_progress=_print_progress)
    print()
    if meta["status"] != "done":
        print(f"Failed: {meta['error']}", file=sys.stderr)
        return 1
    for warning in meta.get("warnings") or []:
        print(f"  Note: {warning}")
    folder = config.LIBRARY_DIR / slug
    print(f"Done: {meta['title']} ({format_duration(meta['duration'])})")
    print(f"  Audio:  {folder / 'audio.mp3'}")
    print(f"  Script: {folder / 'script.md'}")
    return 0


def _add(args) -> int:
    from . import library

    path = Path(args.file).expanduser()
    meta = library.create_reading(path.name, path.read_bytes(), voice=args.voice, title=args.title)
    print(f"Added '{meta['title']}' as {meta['slug']}")
    return _run(meta["slug"])


def _list(_args) -> int:
    from . import library
    from .audio import format_duration

    readings = library.list_readings()
    if not readings:
        print("No readings yet. Add one with: audiobook add path/to/reading.pdf")
    for meta in readings:
        length = format_duration(meta["duration"]) if meta.get("duration") else "-"
        print(f"{meta['slug']:<45} {meta['status']:<10} {length:>8}  {meta['title']}")
    return 0


def _rebuild(args) -> int:
    from . import library

    library.load_meta(args.slug)
    if args.voice:
        library.update_meta(args.slug, voice=args.voice)
    if args.script:
        library.reset_script(args.slug)
    return _run(args.slug)  # reuses script.md unless --script cleared it


def _voices(args) -> int:
    from .voices import make_samples

    print(f"Writing voice samples to {config.VOICE_SAMPLES_DIR}")
    make_samples(force=args.force)
    return 0


def _models(_args) -> int:
    from google import genai

    if not config.has_gemini_key():
        print("No Gemini API key yet: add GEMINI_API_KEY to .env first.", file=sys.stderr)
        return 1
    for model in genai.Client().models.list():
        if "generateContent" in (model.supported_actions or []):
            name = (model.name or "").removeprefix("models/")
            print(f"{name:<40} {model.display_name or ''}")
    print(f"\nCurrently using: {config.GEMINI_MODEL}"
          + (f" (fallback {config.GEMINI_FALLBACK_MODEL})" if config.GEMINI_FALLBACK_MODEL else ""))
    return 0


def main(argv=None) -> int:
    from .voices import VOICES

    parser = argparse.ArgumentParser(prog="audiobook", description="Turn readings into audio.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="start the web app")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--open", action="store_true", help="open it in your browser")
    p.set_defaults(func=_serve)

    p = sub.add_parser("add", help="add a PDF (or .md/.txt) and process it now")
    p.add_argument("file")
    p.add_argument("--title")
    p.add_argument("--voice", default=config.DEFAULT_VOICE, choices=list(VOICES))
    p.set_defaults(func=_add)

    p = sub.add_parser("list", help="list readings")
    p.set_defaults(func=_list)

    p = sub.add_parser("rebuild", help="re-record a reading, e.g. after editing its script.md")
    p.add_argument("slug")
    p.add_argument("--voice", choices=list(VOICES))
    p.add_argument("--script", action="store_true",
                   help="also ask Gemini for a fresh script (uses free-tier requests)")
    p.set_defaults(func=_rebuild)

    p = sub.add_parser("voices", help="record a short sample of every voice")
    p.add_argument("--force", action="store_true", help="re-record existing samples")
    p.set_defaults(func=_voices)

    p = sub.add_parser("models", help="list the Gemini models your key can use")
    p.set_defaults(func=_models)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
