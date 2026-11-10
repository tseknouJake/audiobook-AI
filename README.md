# Audiobook AI

Turns course readings into audio you can follow along with. Upload a PDF.
Gemini turns it into a clean listening script, without references, page
numbers or citations, and with equations and tables described rather than read
out symbol by symbol. Kokoro then records it on your Mac. The player
highlights each sentence as it's read.

- **Text clean-up:** Gemini API, free tier. It reads the PDF pages directly, so scanned chapters work too.
- **Voice:** [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), running locally. Free, and about 18× faster than real time on an M3 Pro.
- **App:** a local web page at <http://127.0.0.1:8000>, plus a command line.

## Setup

You need [uv](https://docs.astral.sh/uv/) and ffmpeg (`brew install uv ffmpeg`).

```bash
uv sync
cp .env.example .env
```

Then get a free API key at [Google AI Studio](https://aistudio.google.com) with
your personal Google account, and paste it after `GEMINI_API_KEY=` in `.env`.
Check which models the key can use, and set `GEMINI_MODEL` to one of them:

```bash
uv run audiobook models
```

The free tier's daily limits are small and change from time to time, and the
full Flash models are often "experiencing high demand". When the main model is
overloaded or out of free requests, the app moves on to the models in
`GEMINI_FALLBACK_MODEL` (Flash-Lite by default). If they all fail, the pages
already done are saved and Retry picks up where it stopped. Google may use
free-tier data to improve its products, so only put published readings
through it.

## Use

```bash
uv run audiobook serve --open      # the web app
```

Or from the terminal:

```bash
uv run audiobook add ~/Downloads/fama-french-1993.pdf
uv run audiobook list
uv run audiobook delete <name>     # move a reading to the Trash can be restored by dragging from trash back
uv run audiobook voices            # record a sample of each voice (play them in the app)
```

`.md` and `.txt` files skip Gemini and are read as written, so they work without a key.

### Player keys

| Key | Does |
|---|---|
| Space | play / pause |
| ← → | previous / next sentence |
| J L | back / forward 15 seconds |
| [ ] | slower / faster |

Click any sentence to jump there. Your place and speed are remembered.

### Pages, tables and figures

For PDFs, **Pages & figures** (or <kbd>P</kbd>) opens the original pages beside
the script:

- **Pages** follows along, keeping the page being read in view and outlined.
  Untick *Follow* to scroll freely. Zoom with − / +, and drag the pane's edge
  to resize it.
- **Tables & figures** lists every table, figure and numbered equation, each
  shown cropped on its own. Click one to see it on its page.
- Mentions like "Table 2" or "Equation 1" in the script are links that jump
  the pane to that item and highlight it.

Tables, figures and equations are found by PyMuPDF's layout model on your Mac,
so this costs no Gemini requests. Scanned PDFs show their pages, but nothing is
listed for them yet.

### Where your files are

Everything lives on your Mac, and stopping the app (Ctrl+C) deletes nothing.

| What | Where |
|---|---|
| Readings: PDF, script, audio, page images | `library/<name>/` in this project folder |
| Voice samples | `voice_samples/` |
| Settings and API key | `.env` |
| Your place and speed in each reading | the browser's local storage |
| The Kokoro voice model (about 330 MB, downloaded once) | `~/.cache/huggingface/` |

`library/`, `voice_samples/` and `.env` are git-ignored, so they're **not** on
GitHub. If you delete this project folder, your readings go with it. To keep
them somewhere backed up, set `AUDIOBOOK_LIBRARY` in `.env` to another folder,
such as one in iCloud Drive, and move the existing `library/` contents there.

To delete a reading, use the bin icon in the library (or
`uv run audiobook delete <name>`). It goes to the macOS Trash, so you can still
get it back.

### Fixing mistakes

Each reading lives in `library/<name>/`. If a sentence comes out wrong, edit
`script.md` there and re-record (in the player, under "Fix a word, or change
the voice", or `uv run audiobook rebuild <name>`). Gemini isn't asked again,
so this uses no free-tier requests. To have Gemini write the script from
scratch, use `uv run audiobook rebuild <name> --script`.

## Code

| File | What it does |
|---|---|
| `src/audiobook_ai/script.py` | PDF → listening script with Gemini (the instructions to tune are at the top) |
| `src/audiobook_ai/segments.py` | script → headings and sentences |
| `src/audiobook_ai/tts.py` | Kokoro text to speech |
| `src/audiobook_ai/audio.py` | records sentence by sentence → `audio.mp3` with chapters + `segments.json` timings |
| `src/audiobook_ai/pdfview.py` | page images and the tables, figures and equations for the side pane |
| `src/audiobook_ai/jobs.py` | runs readings one at a time in the background |
| `src/audiobook_ai/web.py`, `static/` | the web app |
| `src/audiobook_ai/cli.py` | the `audiobook` command |

Ideas and known gaps are in [TODO.md](TODO.md).
