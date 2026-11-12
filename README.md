# Audiobook AI

Turns course readings into audio you can follow along with. Upload a PDF.
Gemini turns it into a clean listening script, without references, page
numbers or citations, and with equations and tables described rather than read
out symbol by symbol. Kokoro then records it on your Mac. The player
highlights each sentence as it's read.

- **Text clean-up:** Gemini API, free tier. It reads the PDF pages directly, so scanned chapters work too.
- **Voice:** [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), running locally. Free, and about 18× faster than real time on an M3 Pro.
- **App:** a local web page at <http://127.0.0.1:8000>, plus a command line.

## Quick start (no coding needed)

You need a Mac with Apple silicon (M1 or newer) and an internet connection.
Nothing else needs installing first.

1. Download this project (on GitHub: **Code › Download ZIP**) and unzip it
   wherever you like.
2. In the folder, double-click **Start Audiobook AI.command**.
   - The first time, macOS may say it can't check the file for malicious
     software. Click **Done**, open **System Settings › Privacy & Security**,
     scroll down and click **Open Anyway** next to "Start Audiobook AI.command".
     You only do this once.
3. A Terminal window opens and sets everything up. The first time it asks to
   install a small tool called uv, then downloads Python and the app (about
   2.5 GB on disk, a few minutes). It all goes inside this folder and nothing
   else on your Mac is changed.
4. It helps you get a free Gemini key: it opens the Google page, and you paste
   the key in.
5. The app opens in your browser. Keep the Terminal window open while you use
   it; close it (or press Ctrl+C) to stop.

Next time, just double-click **Start Audiobook AI.command** again. It checks
everything in a few seconds, then opens the app. If it's already running, it
only opens the browser. From Terminal, `./run.sh` does the same.

**To uninstall:** quit it (close the Terminal window), then drag the folder to
the Trash. That's everything. Your readings are in its `library` folder, so
move that somewhere first if you want to keep them.

(If you already had uv before running this, the launcher uses your existing uv
and its usual caches instead, so those stay where uv keeps them.)

## Setup for developers

`./run.sh` does all of this. To do it by hand you need
[uv](https://docs.astral.sh/uv/); ffmpeg is optional, since a copy comes with
the `imageio-ffmpeg` package.

```bash
uv sync
uv run audiobook setup      # add and check your Gemini key, pick models
```

`audiobook setup` saves the key in `.env`. To edit `.env` yourself instead,
copy `.env.example` to `.env`, get a free key at
[Google AI Studio](https://aistudio.google.com/apikey) with your personal
Google account, and paste it after `GEMINI_API_KEY=`. Then see which models
the key can use:

```bash
uv run audiobook models
```

After Gemini writes a script, the app checks every paragraph of the PDF against
it. Gemini sometimes skips a boxed case study or sidebar; when it does, that
text is sent back on its own and added on its page, and the reading's card says
so. Footnotes, sources, disclaimers, tables and appendices are left out on
purpose and aren't flagged. To run the check on an older reading, use
`uv run audiobook rebuild <name> --check`. It only re-records if something
was added.

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
uv run audiobook add paper.pdf --folder "Financial Economics/Week 3"   # straight into a folder
uv run audiobook move <name> "Financial Economics/Week 4"             # "" for the top level
```

`.md` and `.txt` files skip Gemini and are read as written, so they work without a key.

### Course folders

Use **New folder** in the library to make one for each course, and folders
inside those for weeks or topics. Open a folder to see what's in it; the path
at the top (Library › Financial Economics › Week 3) takes you back up.

- Readings you add while a folder is open go into that folder.
- Move a reading with its folder button, or drag it onto a folder or onto a
  name in the path. Folders can be dragged the same way.
- Inside a folder, **Rename** and **Move** act on that folder, and everything
  in it comes along. The bin only deletes a folder once it's empty.

Folders are labels kept in `library/folders.json` and each reading's
`meta.json`, so moving things around never moves files on disk.

### Player keys

| Key | Does |
|---|---|
| Space | play / pause |
| ← → | previous / next sentence |
| J L | back / forward 15 seconds |
| [ ] | slower / faster |
| P | show or hide the side pane |
| A | open **Ask** and start typing a question |

Click any sentence to jump there. Your place and speed are remembered.

### Ask about the reading

The **Ask** tab in the side pane (or <kbd>A</kbd>) answers questions about the
reading, from where you are in it:

- **"This" is what you're on:** the sentence being read, or text you select in
  the script. So "what does this mean?" or "what did the previous paragraph
  mean?" just work. The line at the top of the tab shows what "this" is.
- **Quick buttons:** Simplify this, Explain this better, Define this term,
  Explain the equation, Why does this matter?, Previous paragraph, Summarise
  this section. Or type your own question; follow-ups like "simpler still" work.
- **Answers say where they come from:** click a page reference such as (p. 6)
  to jump there in the script. Each answer has a Copy button for your notes.
- **Asking pauses the audio.** Chats are saved per reading; *Clear this chat*
  empties it.

Nothing is sent to Gemini until you ask. Each question is one request carrying
the whole reading with your place marked. Ask uses its own models
(`GEMINI_CHAT_MODELS`, Gemini 3.5 Flash with Flash-Lite as a fallback), separate
from the script-writing ones, so it can't use up their free allowance. It also
stops at `AUDIOBOOK_CHAT_DAILY_LIMIT` questions a day (50 by default), counted
down at the bottom of the tab. Quick buttons usually answer in a few seconds;
typed questions can take longer when Gemini is busy, and it switches to
Flash-Lite after about 45 seconds.

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
| Python packages | `.venv/` |
| uv, Python, the download cache and the voice model (about 330 MB), when the launcher installed uv | `.tools/` |
| The voice model if you already had uv | `~/.cache/huggingface/` |

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
| `src/audiobook_ai/chat.py` | the Ask tab: builds the question with your place marked, its own models, the daily cap |
| `src/audiobook_ai/jobs.py` | runs readings one at a time in the background |
| `src/audiobook_ai/web.py`, `static/` | the web app |
| `src/audiobook_ai/cli.py` | the `audiobook` command |

Ideas and known gaps are in [TODO.md](TODO.md).
