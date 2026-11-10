"""Record a script sentence by sentence, then save one MP3 plus timestamps."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .segments import parse_script
from .tts import SAMPLE_RATE, synthesize

# Seconds of silence between pieces.
PAUSE_SENTENCE = 0.25
PAUSE_PARAGRAPH = 0.6
PAUSE_BEFORE_HEADING = 0.9
PAUSE_AFTER_HEADING = 0.5


def _pause_before(segment: dict, previous: dict | None) -> float:
    if previous is None:
        return 0.0
    if segment["kind"] == "heading":
        return PAUSE_BEFORE_HEADING
    if previous["kind"] == "heading":
        return PAUSE_AFTER_HEADING
    if segment["para"] != previous["para"]:
        return PAUSE_PARAGRAPH
    return PAUSE_SENTENCE


def build_audio(reading_dir: Path, voice: str, title: str, progress=lambda done, total: None) -> dict:
    """Read script.md aloud into audio.mp3 and write segments.json.

    segments.json lists every heading and sentence with its start and end time
    in seconds, which is what the read-along player highlights.
    """
    script = (reading_dir / "script.md").read_text(encoding="utf-8")
    segments = parse_script(script)
    if not segments:
        raise ValueError("The script is empty, so there is nothing to read.")
    # Leftovers from a recording that was interrupted (the caller holds the
    # reading's lock, so nothing else is recording it now).
    for stale in reading_dir.glob("tmp*"):
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
    (reading_dir / "audio.partial.mp3").unlink(missing_ok=True)

    timed = []
    samples_written = 0
    with tempfile.TemporaryDirectory(dir=reading_dir) as tmp:
        wav_path = Path(tmp) / "audio.wav"
        with sf.SoundFile(wav_path, "w", SAMPLE_RATE, 1, subtype="PCM_16") as wav:
            previous = None
            for i, segment in enumerate(segments):
                gap = int(_pause_before(segment, previous) * SAMPLE_RATE)
                if gap:
                    wav.write(np.zeros(gap, dtype=np.float32))
                    samples_written += gap
                speech = np.clip(synthesize(segment["text"], voice), -1.0, 1.0)
                start = samples_written / SAMPLE_RATE
                wav.write(speech)
                samples_written += len(speech)
                timed.append({"id": i, **segment, "start": round(start, 3),
                              "end": round(samples_written / SAMPLE_RATE, 3)})
                previous = segment
                progress(i + 1, len(segments))
            wav.write(np.zeros(int(0.5 * SAMPLE_RATE), dtype=np.float32))

        duration = samples_written / SAMPLE_RATE
        chapters = _chapters(timed, duration)
        partial = reading_dir / "audio.partial.mp3"
        wav_to_mp3(wav_path, partial, title=title, chapters=chapters)
        partial.replace(reading_dir / "audio.mp3")

    data = {"duration": round(duration, 3), "voice": voice, "chapters": chapters, "segments": timed}
    (reading_dir / "segments.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _chapters(timed: list[dict], duration: float) -> list[dict]:
    heads = [s for s in timed if s["kind"] == "heading" and s["level"] <= 3]
    chapters = [{"title": h["text"], "start": h["start"]} for h in heads]
    if not chapters or chapters[0]["start"] > 5:
        chapters.insert(0, {"title": "Beginning", "start": 0.0})
    chapters[0]["start"] = 0.0
    for current, following in zip(chapters, chapters[1:] + [None]):
        current["end"] = following["start"] if following else round(duration, 3)
    return chapters


def _escape(value: str) -> str:
    for ch in "\\=;#\n":
        value = value.replace(ch, "\\" + ch)
    return value


def wav_to_mp3(wav_path: Path, out_path: Path, title: str = "", chapters: list[dict] | None = None) -> None:
    """Encode to constant-bitrate MP3 (so seeking lands exactly), with chapters."""
    lines = [";FFMETADATA1", f"title={_escape(title)}", "artist=Audiobook AI"]
    for ch in chapters or []:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(ch['start'] * 1000)}",
                  f"END={int(ch['end'] * 1000)}", f"title={_escape(ch['title'])}"]
    meta_path = wav_path.with_suffix(".meta.txt")
    meta_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-i", str(meta_path),
         "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
         "-codec:a", "libmp3lame", "-b:a", "64k", "-ar", str(SAMPLE_RATE), "-ac", "1",
         "-f", "mp3", str(out_path)],
        check=True,
    )


def encode_mp3(pieces, out_path: Path, title: str = "") -> None:
    """Encode a few in-memory clips (used for voice samples)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "clip.wav"
        with sf.SoundFile(wav_path, "w", SAMPLE_RATE, 1, subtype="PCM_16") as wav:
            for piece in pieces:
                wav.write(np.clip(piece, -1.0, 1.0))
        wav_to_mp3(wav_path, out_path, title=title)


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
