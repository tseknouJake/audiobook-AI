"""Check that a code change leaves the recorded audio alone.

    uv run python scripts/compare_audio.py                  # list the readings
    uv run python scripts/compare_audio.py bonds            # main vs the code as it is now
    uv run python scripts/compare_audio.py bonds --against other-branch --sentences 60

The opening of a reading is recorded by both versions of the code, in a
temporary copy (your library isn't touched), and the recordings land in
comparisons/<reading>/ in this folder (ignored by git):

- before.mp3 and after.mp3 are recorded exactly the way the app records.
  Listen to these.
- before-fixed.mp3 and after-fixed.mp3 are recorded with the voice's built-in
  randomness pinned down, so they can be compared sample by sample. If they
  match, the change can't have altered the sound.

Kokoro adds a little random variation every time it speaks (part of why it
sounds natural), so two normal recordings never match sample for sample, even
from the same code. That's why the pinned-down pair is the real test.
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 1234
RATE = 24000
DEFAULT_SENTENCES = 110  # past the memory clean-ups at sentences 50 and 100
RUNS = [  # (file name, which code, randomness pinned down)
    ("before-fixed", "before", True),
    ("after-fixed", "after", True),
    ("before", "before", False),
    ("after", "after", False),
]


def record(code: Path, folder: Path, voice: str, fixed: bool) -> None:
    """Runs in a child process: record folder/script.md with the code in `code`."""
    sys.path.insert(0, str(code / "src"))
    import torch
    from audiobook_ai import audio

    if not Path(audio.__file__).resolve().is_relative_to(code.resolve()):
        sys.exit(f"Loaded the wrong copy of the code: {audio.__file__}")

    speak = audio.synthesize

    def synthesize(text, voice):
        if fixed:
            torch.manual_seed(SEED)
        return speak(text, voice)

    audio.synthesize = synthesize
    cleanups = [0]
    if hasattr(audio, "release_memory"):  # only in code that has the clean-ups
        release = audio.release_memory

        def counted():
            cleanups[0] += 1
            release()

        audio.release_memory = counted
    started = time.monotonic()
    audio.build_audio(folder, voice, "comparison")
    print(json.dumps({"seconds": round(time.monotonic() - started), "cleanups": cleanups[0]}))


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout


def export(ref: str | None, dest: Path) -> None:
    """Put a copy of src/ in dest: from a branch or commit, or as it is now (None)."""
    dest.mkdir()
    if ref is None:
        shutil.copytree(ROOT / "src", dest / "src", ignore=shutil.ignore_patterns("__pycache__"))
    else:
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", ref, "src"],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)


def sentence_count(script: str) -> int:
    from audiobook_ai.segments import parse_script
    return sum(s["kind"] == "sentence" for s in parse_script(script))


def opening(script: str, count: int) -> str:
    """The script up to the end of the paragraph that reaches `count` sentences."""
    if not count:
        return script
    blocks = script.split("\n\n")
    for end in range(1, len(blocks) + 1):
        text = "\n\n".join(blocks[:end])
        if sentence_count(text) >= count:
            return text + "\n"
    return script


def samples(mp3: Path):
    import numpy as np
    from audiobook_ai.audio import ffmpeg_path
    raw = subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(mp3), "-f", "f32le",
                          "-ac", "1", "-ar", str(RATE), "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def loudness(x) -> str:
    import numpy as np
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return f"{20 * math.log10(rms):.1f} dB" if rms else "silent"


def pick_reading(name: str | None) -> dict | None:
    from audiobook_ai import library
    readings = [m for m in library.list_readings()
                if (library.reading_dir(m["slug"]) / "script.md").exists()]
    if name:
        matches = [m for m in readings if m["slug"] == name] or [
            m for m in readings if name.lower() in f"{m['slug']} {m['title']}".lower()]
        if len(matches) == 1:
            return matches[0]
        print(f"No reading matches {name!r}." if not matches else f"{name!r} matches more than one reading.")
        readings = matches or readings
    print("Which reading? Give its name, or part of its title:")
    for m in readings:
        print(f"  {m['slug']:<55} {m['title']}")
    return None


def main() -> None:
    if sys.argv[1:2] == ["--record"]:
        code, folder, voice, mode = sys.argv[2:]
        record(Path(code), Path(folder), voice, mode == "fixed")
        return

    parser = argparse.ArgumentParser(description="Record the same reading with two versions of the "
                                                 "code and check the audio is the same.")
    parser.add_argument("reading", nargs="?", help="the reading's name, or part of its title")
    parser.add_argument("--against", default="main",
                        help="the branch or commit to compare the current code with (default: main)")
    parser.add_argument("--sentences", type=int, default=DEFAULT_SENTENCES,
                        help=f"how much to record (default {DEFAULT_SENTENCES}; 0 for the whole reading)")
    args = parser.parse_args()

    meta = pick_reading(args.reading)
    if meta is None:
        sys.exit(1)
    try:
        git("rev-parse", "--verify", "--quiet", f"{args.against}^{{commit}}")
    except subprocess.CalledProcessError:
        sys.exit(f"There's no branch or commit called {args.against!r}.")
    changed = git("diff", "--name-only", args.against, "--", "src").split()
    if not changed:
        sys.exit(f"The code is the same as {args.against}, so there's nothing to compare.")

    from audiobook_ai import library
    script = opening((library.reading_dir(meta["slug"]) / "script.md").read_text(encoding="utf-8"),
                     args.sentences)
    print(f"Reading: {meta['title']} ({sentence_count(script)} sentences, voice {meta['voice']})")
    print(f"Before:  {args.against}")
    print(f"After:   the code as it is now, which differs in {', '.join(Path(f).name for f in changed)}\n")

    out = ROOT / "comparisons" / meta["slug"]
    results, timings = {}, {}
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "HF_HUB_VERBOSITY": "error"}
    with tempfile.TemporaryDirectory(prefix="audiobook-compare-") as tmp:
        tmp = Path(tmp)
        export(args.against, tmp / "code-before")
        export(None, tmp / "code-after")
        for number, (name, side, fixed) in enumerate(RUNS, 1):
            folder = tmp / "recordings" / name
            folder.mkdir(parents=True)
            (folder / "script.md").write_text(script, encoding="utf-8")
            print(f"Recording {number} of {len(RUNS)}: {name}.mp3 …", end=" ", flush=True)
            done = subprocess.run([sys.executable, __file__, "--record", str(tmp / f"code-{side}"), str(folder),
                                   meta["voice"], "fixed" if fixed else "normal"],
                                  capture_output=True, text=True, env=env)
            if done.returncode:
                print("failed:\n" + done.stderr[-3000:])
                sys.exit(1)
            results[name] = json.loads([line for line in done.stdout.splitlines() if line.startswith("{")][-1])
            timings[name] = json.loads((folder / "segments.json").read_text(encoding="utf-8"))
            print(f"{results[name]['seconds']} s")
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for name, _, _ in RUNS:
            shutil.copy(tmp / "recordings" / name / "audio.mp3", out / f"{name}.mp3")

    import numpy as np
    a, b = samples(out / "before-fixed.mp3"), samples(out / "after-fixed.mp3")
    same = np.array_equal(a, b) and timings["before-fixed"] == timings["after-fixed"]
    print("\nThe real test, with the voice's randomness pinned down:")
    if same:
        print(f"  IDENTICAL, sample for sample, all {clock(len(a) / RATE)} of it, and the sentence "
              f"timings match too.")
    else:
        n = min(len(a), len(b))
        print(f"  DIFFERENT: lengths {clock(len(a) / RATE)} and {clock(len(b) / RATE)}, largest "
              f"difference {float(np.abs(a[:n] - b[:n]).max()):.4f} (on a scale of -1 to 1). "
              f"Listen to before-fixed.mp3 and after-fixed.mp3 to hear it.")
    cleanups = results["after-fixed"]["cleanups"]
    if cleanups:
        print(f"  The new code cleared memory {'once' if cleanups == 1 else f'{cleanups} times'} during it.")

    print("\nRecorded normally, the way the app does it (for listening):")
    for name in ("before", "after"):
        x = samples(out / f"{name}.mp3")
        r = results[name]
        print(f"  {name + '.mp3':<11} {clock(len(x) / RATE)} long, loudness {loudness(x)}, "
              f"took {r['seconds']} s")
    print("  These two never match sample for sample, even from the same code: the voice varies a\n"
          "  little each time it speaks. They should sound the same.")
    print(f"\nFiles: {out}\n  open \"{out}\"")


if __name__ == "__main__":
    main()
