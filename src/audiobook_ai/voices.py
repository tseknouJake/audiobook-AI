"""The Kokoro voices offered in the app, and short samples of each."""

from pathlib import Path

from .config import VOICE_SAMPLES_DIR

# id -> label. The first letter of the id is the accent (a = American,
# b = British), the second the gender. Ordered roughly best first.
VOICES = {
    "af_heart": "Heart · US female",
    "af_bella": "Bella · US female",
    "af_nicole": "Nicole · US female, soft",
    "af_kore": "Kore · US female",
    "af_sarah": "Sarah · US female",
    "am_michael": "Michael · US male",
    "am_fenrir": "Fenrir · US male",
    "am_puck": "Puck · US male",
    "bf_emma": "Emma · UK female",
    "bf_isabella": "Isabella · UK female",
    "bm_george": "George · UK male",
    "bm_fable": "Fable · UK male",
}

SAMPLE_TEXT = (
    "The capital asset pricing model predicts that a stock's expected excess return "
    "rises in proportion to its beta. In the data, however, low-beta stocks have earned "
    "higher risk-adjusted returns than the model implies. Table 2 reports the results; "
    "the effect holds across size groups and survives controls for value and momentum."
)


def sample_path(voice: str) -> Path:
    return VOICE_SAMPLES_DIR / f"{voice}.mp3"


def make_samples(force: bool = False, log=print) -> None:
    from .audio import encode_mp3
    from .tts import synthesize

    VOICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for voice, label in VOICES.items():
        out = sample_path(voice)
        if out.exists() and not force:
            log(f"  {voice}: already exists")
            continue
        log(f"  {voice}: {label}")
        encode_mp3([synthesize(SAMPLE_TEXT, voice)], out, title=label)
