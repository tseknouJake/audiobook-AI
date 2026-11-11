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


ESPEAK_MAX_PATH = 150  # espeak-ng fails from 160 characters (measured); keep a margin


def _use_short_espeak_path() -> None:
    """espeak-ng (which Kokoro uses for words it doesn't know) can't use a data
    folder whose real path is 160 characters or more, and then fails. That
    happens when the project folder sits deep inside other folders, so in that
    case keep a copy (about 18 MB, made once) somewhere shorter: the folder's
    .tools/cache when run.sh set one up, otherwise ~/.cache/audiobook-ai."""
    import os
    import shutil
    from importlib.metadata import version
    from pathlib import Path

    import espeakng_loader
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    source = Path(espeakng_loader.get_data_path()).resolve()
    if len(str(source)) <= ESPEAK_MAX_PATH:
        EspeakWrapper.set_data_path(str(source))
        return
    name = f"espeak-ng-data-{version('espeakng-loader')}"
    for base in (os.environ.get("AUDIOBOOK_CACHE"), str(Path.home() / ".cache" / "audiobook-ai")):
        if not base:
            continue
        short = Path(base).resolve() / name
        if len(str(short)) > ESPEAK_MAX_PATH:
            continue
        if not (short / "phontab").exists():
            partial = short.with_name(name + ".partial")
            shutil.rmtree(partial, ignore_errors=True)
            shutil.copytree(source, partial)
            partial.replace(short)
        EspeakWrapper.set_data_path(str(short))
        return
    raise RuntimeError("The app's folder is too deep inside other folders for the voice software. "
                       "Move it somewhere shorter, such as your Documents folder, and start it again.")


def _pipeline(voice: str):
    """One shared model; one pipeline per accent ('a' = US, 'b' = UK)."""
    global _model
    lang = voice[0]
    with _lock:
        if lang not in _pipelines:
            warnings.filterwarnings("ignore", category=UserWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            import logging

            # A note torch prints on every Mac start ("Redirects are currently not supported")
            logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)
            from kokoro import KModel, KPipeline
            import misaki.espeak  # noqa: F401  (sets espeak's paths; overridden next)

            _use_short_espeak_path()

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
