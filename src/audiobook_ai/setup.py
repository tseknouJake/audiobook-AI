"""First-run setup: get a Gemini API key, check it works, and pick models.

Used by `audiobook setup` (which run.sh calls every time it starts). With a
working key already saved it just checks it and returns, in about a second.
"""

import os
import re
import webbrowser

from . import config

KEY_PAGE = "https://aistudio.google.com/apikey"
ENV_FILE = config.PROJECT_ROOT / ".env"
ENV_EXAMPLE = config.PROJECT_ROOT / ".env.example"

GUIDE = f"""
PDFs are turned into listening scripts by Google's Gemini, which is free with
your own key. Getting one takes about two minutes:

  1. Open {KEY_PAGE}
  2. Sign in with your personal Google account
  3. Click "Create API key" (accept the terms if it asks)
  4. Copy the key. It's a long line of letters and numbers starting with "AIza"

Good to know: on the free tier, Google may use what you send to improve its
products. That's fine for published readings; don't put anything private
through it.
"""


def read_env() -> dict[str, str]:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def set_env(name: str, value: str) -> None:
    """Set NAME=value in .env, replacing the line if it's there."""
    if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
        ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pattern = re.compile(rf"^\s*#?\s*{name}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_FILE.chmod(0o600)  # only you can read it: it holds your key
    os.environ[name] = value


class KeyProblem(Exception):
    """The key was rejected (as opposed to Google being unreachable)."""


def available_models(key: str) -> list[str]:
    """Names of the models this key can generate text with. Raises KeyProblem for
    a bad key, and other exceptions when Google can't be reached."""
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)))
    try:
        return [(m.name or "").removeprefix("models/") for m in client.models.list()
                if "generateContent" in (m.supported_actions or [])]
    except errors.ClientError as exc:
        if exc.code in (400, 401, 403):
            raise KeyProblem(exc.message or str(exc)) from None
        raise


def _version(name: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", name.split("-")[1])) if "-" in name else ()


def choose_models(names: list[str], current: str, fallbacks: list[str]) -> tuple[str, list[str]]:
    """Keep the configured models if this key has them; otherwise the newest
    Flash model as the main one and the newest Flash-Lite ones as fallbacks."""
    flash = sorted((n for n in names if re.fullmatch(r"gemini-[\d.]+-flash", n)), key=_version, reverse=True)
    lite = sorted((n for n in names if re.fullmatch(r"gemini-[\d.]+-flash-lite", n)), key=_version, reverse=True)
    main = current if current in names else (flash or lite or [current])[0]
    kept = [f for f in fallbacks if f in names and f != main]
    return main, kept or [n for n in lite if n != main][:2]


def _save_models(names: list[str]) -> None:
    env = read_env()
    current = env.get("GEMINI_MODEL", config.GEMINI_MODEL)
    fallbacks = [f.strip() for f in env.get("GEMINI_FALLBACK_MODEL", "").split(",") if f.strip()]
    main, kept = choose_models(names, current, fallbacks)
    if main != current:
        print(f"  Using {main} (your key doesn't have {current}).")
        set_env("GEMINI_MODEL", main)
    if kept != fallbacks:
        set_env("GEMINI_FALLBACK_MODEL", ",".join(kept))


def run(interactive: bool = True) -> int:
    """Make sure there's a working Gemini key. Returns 0 even when skipped:
    .md and .txt readings work without one."""
    if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
        ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        ENV_FILE.chmod(0o600)

    key = read_env().get("GEMINI_API_KEY", "")
    if key:
        try:
            _save_models(available_models(key))
            print("  Gemini key: working.")
            return 0
        except KeyProblem as exc:
            print(f"  Your saved Gemini key didn't work ({exc}). Let's add a new one.")
        except Exception:
            print("  Couldn't reach Google to check your Gemini key (are you offline?). Carrying on.")
            return 0

    if not interactive:
        print("  No Gemini key yet: PDFs won't work until you add one. Run ./run.sh again to add it.")
        return 0

    print(GUIDE)
    try:
        if input("Open that page in your browser now? [Y/n] ").strip().lower() in ("", "y", "yes"):
            webbrowser.open(KEY_PAGE)
        while True:
            key = input("\nPaste your key here, then press Enter (or just press Enter to skip): ").strip().strip('"\'')
            if not key:
                print("  Skipped. PDFs won't work until you add a key; .md and .txt files will.\n"
                      "  Run ./run.sh again any time to add it.")
                return 0
            if not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", key):
                print("  That doesn't look like a key. It should be one long line of letters and numbers.")
                continue
            try:
                names = available_models(key)
            except KeyProblem as exc:
                print(f"  Google didn't accept that key: {exc}\n  Check you copied all of it, and try again.")
                continue
            except Exception as exc:
                print(f"  Couldn't reach Google to check it ({type(exc).__name__}). Saving it anyway.")
                set_env("GEMINI_API_KEY", key)
                return 0
            set_env("GEMINI_API_KEY", key)
            _save_models(names)
            print("  Key saved. You're all set.")
            return 0
    except (EOFError, KeyboardInterrupt):
        print("\n  Skipped adding a key for now.")
        return 0
