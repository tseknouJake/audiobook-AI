# TODO

## Next up: share it with friends (who have never coded)
Plan: a Mac app they download, with everything set up from inside the app. A hosted website is the alternative if lots of people want it or they need phones or Windows (that needs accounts, per-person libraries and keys, and you looking after a server).
- [x] First step: `run.sh` / **Start Audiobook AI.command** (double-click). It installs uv, Python and the packages, helps you get a Gemini key and checks it (`audiobook setup`), downloads the voices, then opens the app. Homebrew isn't needed (ffmpeg comes from `imageio-ffmpeg`). Tested on a fresh copy
- [ ] Setup screen in the app for the Gemini key (can reuse `src/audiobook_ai/setup.py`)
  - A "Get your free Gemini key" guide with numbered steps and screenshots (aistudio.google.com → Get API key → copy), saying plainly that Google may use free-tier data
  - Paste box with a "Test key" button that checks it works and picks the Gemini models automatically (no editing `.env`, no `audiobook models`)
- [ ] Nothing needs the terminal: voice samples made in the app, and the voice model (about 330 MB) downloaded on first run with a progress bar
- [ ] Package as a Mac app: a small download that fetches the rest on first launch, then opens in the browser
  - The tutorial covers the one-time "Open Anyway" step (System Settings → Privacy & Security), since the app isn't registered with Apple (registering costs $99 a year)
  - Works well on M1 or newer Macs and slowly on Intel ones; Windows needs extra work (the reading lock uses `fcntl`, which Windows doesn't have)
- [ ] A "new version available" notice so updating is one click
- [ ] Check before sharing: the Gemini free tier is available in friends' countries; PyMuPDF is AGPL, which is fine while the project stays open source

## Test with real readings first
- [x] Add a Gemini API key and test with a made-up 2-page paper (citations, equation, table, references all handled)
- [ ] Run a few real readings (a text PDF, a scanned chapter, an equation-heavy paper)
- [ ] Compare Flash with Flash-Lite on the same reading once Flash isn't overloaded; Flash-Lite sometimes leaves citations in (a safety net now strips them)
- [x] Skipped boxes and case studies: Gemini is told to keep them, and a check compares every paragraph of the PDF with the script, re-asks for what's missing and names any pages still missing text (fixed the Ecuador case study in "Bonds and (Planetary) Boundaries 2.0")
- [ ] The skipped-text check ignores bullet lists (the layout model files them under "list-item", which also covers table contents); see whether real lists get skipped too
- [ ] Check Gemini keeps the whole text and doesn't quietly summarise (the app warns when the script looks short)
- [ ] Check whether Gemini refuses pages of published papers ("RECITATION"); if it happens often, try smaller chunks or a different model

## Cleaning up the text
Handled by the Gemini instructions in `src/audiobook_ai/script.py`; tune them on real readings.
- [ ] Reference lists, page numbers, running headers and footers are skipped
- [ ] In-text citations removed or softened ("Fama and French (1993) show" → "Fama and French show")
- [ ] URLs, DOIs and footnote markers dropped; decide whether footnotes should be read
- [ ] Equations: read short ones in words ("beta equals the covariance of…"), and summarise long ones ("Equation 3 says expected excess returns rise with beta")
- [ ] Tables and regression output: never read cell by cell; give one or two sentences on the main finding plus "see Table 2"
- [ ] Notation: say β, α and σ properly, plus acronyms like CAPM, OLS and GARCH
- [ ] Pronunciation list for words the voice gets wrong (author names, jargon)
- [ ] Appendices are skipped with a one-line mention; make that a setting

## Player
- [x] Original page beside the player, so you can see the real equation or table while taking notes (follows along, clickable "Table 2" mentions, Tables & figures tab)
- [ ] Bookmark key: mark a sentence while listening, then export your marks with your notes
- [ ] Try embedding images of charts, tables and equations straight into the script text, where they're discussed (an alternative to the side pane, which works well, so keep it). The crops from the Tables & figures tab could be reused; place each after the sentence that mentions it
- [ ] Tables & figures for scanned PDFs: they have no text layer, so nothing is detected yet; Gemini could return the boxes while writing the script
- [ ] A word hyphenated across a page break is rejoined without its hyphen, so "low-beta" split there would become "lowbeta"
- [ ] Sentences split in two across a page break: "…particularly those who are looking for" then a new paragraph "stable, long-term exposure to climate and nature-related assets." That gives a pause in the audio and two highlights instead of one sentence. Cause: Gemini sometimes puts a blank line plus the page marker in the middle of a sentence (the code only fixes markers that sit inside a paragraph with no blank line), and the break between 10-page chunks can do the same. Fix: when a paragraph doesn't end with . ? ! : or a closing quote and the next text (past any page marker) starts with a lower-case letter, join the two and move the marker after the paragraph; do it both in script.py and as a safety net in segments.py

## Study aids
- [ ] Notes and highlights for the important sections: key arguments, definitions and findings marked in the player and exported as notes. Deciding what's important is the hard part; a first version could have Gemini mark key sentences when it writes the script
- [ ] A 60-second "here's the argument" intro before the reading starts
- [ ] A short recap at the end of each section
- [ ] 3–5 recall questions at the end
- [ ] Reading chatbot: ask about what you're reading or listening to (spec below)

### Reading chatbot: spec for a first version

**What it is:** a chat panel in the player. You can ask anything about the reading, and it already knows the paper and the part you're on, so "simplify this" or "explain this better" just work.

**What Gemini is given with every question**
- The whole script (`script.md`), plus the title and section headings. A reading is a small fraction of what Gemini can take in, so there's no search step.
- **"This" = what you're looking at**, in order of priority:
  1. Text you've selected in the script, if any
  2. Otherwise the sentence being read, with the paragraph around it
  3. Plus the section heading it's under and its page number
- The chat so far for this reading, so follow-ups like "simpler still" work.
- Instructions: answer from the reading; say clearly when the reading doesn't cover something rather than guessing; keep answers short unless asked for more; plain language; no symbols the voice can't read.

**In the player**
- An "Ask" tab in the side pane (next to Pages and Tables & figures), plus a keyboard shortcut to open it with the cursor in the box.
- Opening it or sending a question pauses the audio. The answer appears there, and the player is ready to carry on from the same spot.
- A small strip above the box shows what "this" is ("About: 'Low-beta portfolios earn…' · page 1"), so you can see what it's talking about and clear it.
- One-click quick questions:
  - **Simplify this**
  - **Explain this better**, with an example
  - **Define a term**, for a selected word
  - **Explain this equation**, when the sentence or selection is about one
  - **Why does this matter?**, for how this part fits the paper's argument
  - **Summarise this section**
- Answers say which page or section they come from, and clicking that jumps the script and the PDF pane there.
- Chats are saved per reading (in the reading's folder) and can be cleared. A "Copy" button on each answer, for pasting into notes.

**Model and free-tier limits**
- Use the fallback list (Flash-Lite first, since it has higher limits), one request per question.
- Show "X questions left today" if the limit can be read, and a clear message when it runs out.
- Optional later: a "Think harder" button that uses a stronger model for tough questions. Free Gemini Flash if it's responding, or a personal Claude API key, which costs roughly 0.1¢ (Haiku) to 5¢ (Opus) per question.

**Before building: test quality**
- Write 8–10 real questions about the Bonds reading, easy and hard. Compare Flash-Lite, Flash and Claude side by side to see whether free Gemini is good enough, and for which kinds of question.

**Not in the first version, for later**
- Ask out loud and hear the answer in the reading's voice (hands-free)
- Quiz mode: recall questions at the end of a section or reading
- "Add to notes": save an answer next to the sentence it's about, exported with bookmarks
- Questions across several readings in a course folder ("how does this paper relate to last week's?")
- Compare with Google's NotebookLM, which does chat over uploaded PDFs for free; what's built in here adds knowing your place in the audio and jumping to the source

## Inputs and outputs
- [ ] Other inputs: EPUB, Word, web articles
- [ ] Private podcast feed for listening away from the desk

## Extensions
- [ ] Run it on the Hetzner server, to use from a phone anywhere
  - Keep it private with Tailscale (`tailscale serve` in front of port 8000); the app has no login, so never expose it publicly
  - Install the CPU-only build of torch on Linux (point uv at PyTorch's CPU index), or it pulls in 2–3 GB of unused GPU libraries
  - Run it as a systemd service; copy `library/` and `.env` over; at least 4 GB of RAM, 8 GB is comfortable
  - Measure Kokoro's speed on the server's CPU first (probably 2–4× real time rather than 18× on the Mac's GPU)
  - Quicker alternative: Tailscale on the Mac and phone to reach the Mac's copy (only while the Mac is awake)
- [ ] For phones: lock-screen controls (Media Session API), a home-screen icon, and checking audio keeps playing with the screen locked
