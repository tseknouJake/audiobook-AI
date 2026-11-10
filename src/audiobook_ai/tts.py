"""Text to speech with Kokoro, run locally (on the Mac's GPU when available)."""

import os
import threading
import warnings

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np

SAMPLE_RATE = 24_000
KOKORO_REPO = "hexgrad/Kokoro-82M"

_lock = threading.Lock()
_model = None
_pipelines: dict[str, object] = {}


def _device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def _pipeline(voice: str):
    """One shared model; one pipeline per accent ('a' = US, 'b' = UK)."""
    global _model
    lang = voice[0]
    with _lock:
        if lang not in _pipelines:
            warnings.filterwarnings("ignore", category=UserWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            from kokoro import KModel, KPipeline

            if _model is None:
                _model = KModel(repo_id=KOKORO_REPO).to(_device()).eval()
            _pipelines[lang] = KPipeline(lang_code=lang, repo_id=KOKORO_REPO, model=_model)
        return _pipelines[lang]


def synthesize(text: str, voice: str) -> np.ndarray:
    """Speak `text` and return mono float32 samples at SAMPLE_RATE."""
    pipeline = _pipeline(voice)
    parts = []
    with _lock:  # the model isn't safe to share across threads
        for result in pipeline(text, voice=voice, speed=1.0, split_pattern=None):
            if result.audio is not None:
                parts.append(result.audio.detach().cpu().numpy())
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32)
