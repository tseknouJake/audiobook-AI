"""Settings, read from the environment and the project's .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# No usage reporting from the libraries. ONNX Runtime (behind the PDF layout
# model) otherwise writes a device ID to ~/Library/Application Support/Microsoft.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

LIBRARY_DIR = Path(os.environ.get("AUDIOBOOK_LIBRARY", PROJECT_ROOT / "library"))
VOICE_SAMPLES_DIR = PROJECT_ROOT / "voice_samples"
STATIC_DIR = Path(__file__).resolve().parent / "static"

DEFAULT_VOICE = os.environ.get("AUDIOBOOK_VOICE", "af_heart")

# Gemini does the text clean-up. Model names change often: run `audiobook models`
# to see what your key can use, then set these in .env.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
# Tried in order when the main model is overloaded or out of free requests.
# One model or a comma-separated list.
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "")
GEMINI_MODELS = [GEMINI_MODEL] + [m.strip() for m in GEMINI_FALLBACK_MODEL.split(",") if m.strip()]

# How many PDF pages go to Gemini per request. Bigger chunks use fewer of the
# free tier's daily requests; smaller ones are less likely to hit output limits.
PAGES_PER_CHUNK = int(os.environ.get("AUDIOBOOK_PAGES_PER_CHUNK", "10"))

# The reading chatbot (Ask tab) has its own models, tried in order, kept apart
# from the script-writing ones so questions don't use up their free allowance.
GEMINI_CHAT_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_CHAT_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
# Questions per day, so the chatbot can never quietly drain the free tier.
CHAT_DAILY_LIMIT = int(os.environ.get("AUDIOBOOK_CHAT_DAILY_LIMIT", "50"))


def has_gemini_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
