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
- [x] **Bug: a page read twice at a chunk boundary.** In "Investing in Nature: African Parks case" (48 pages), the Gemini part for pages 28–37 covered all of page 37 (ending "…transparently report outcomes."). The part for pages 37–46 was told to continue after that, but Flash-Lite started page 37 again from the top. Page 37 starts mid-sentence, so the repeat began with the stray fragment "has been maintained, whilst the surrounding landscape has been dramatically transformed." and about 400 words (roughly 2½ minutes) played twice. The original sentence ("Over the past twenty years, Majete Wildlife Reserve's intactness | has been maintained…") was also split across the page break, which is the split-sentence bug under Player. Fix: after each chunk, compare its opening sentences with the end of the previous chunk (matching runs of words) and drop anything already covered; or stop repeating the last page in the next chunk and instead join a sentence cut off at the chunk break. Also add a duplicate check next to the skipped-text check, so repeated passages are caught and named on the reading's card
  - Fixed in `stitch.py`: the start of each part is compared with the end of the script so far and repeats are cut (the African Parks reading loses its 360-word repeat of page 37, and nothing else changes). Sentences split across a page or part break are joined again, and a word split at a break keeps its hyphen when it's a compound ("low-beta") and closes up when it isn't ("pattern"). Any repeat that's left gets a note on the reading's card
- [x] **Rule: picking up again must be seamless, whatever the reason.** Each part must continue exactly where the last stopped, with nothing repeated or skipped. That applies at every 10-page break; when the app is closed or crashes mid-reading and Retry carries on; when the free limit runs out and it resumes later; and when it switches model halfway (Flash to Flash-Lite)
  - Save the exact stopping point with each finished part: the page and the last full sentence. Every resume sends that context ("the script so far ends with: …, start with the very next sentence"), however much time has passed
  - Don't rely on Gemini following that instruction. Check in code: compare the new part's opening sentences with the script so far and cut any overlap; then the skipped-text check confirms nothing in between is missing; then join a sentence cut off at the break
  - A half-finished part is never kept: only parts that finished cleanly are saved (as now), so a crash mid-request just redoes that part
  - Test: stop a long reading after each part in turn (and switch model partway), resume, and check the final script matches an uninterrupted run, with no repeats or gaps
- [x] **Bug: whole pages missing, and the acknowledgements read out in the middle of section 2.** "Climate Risk Disclosure and Institutional Investors" and "Three Decades of Global Institutional Investment" were both written by Flash-Lite on 14 Nov (Flash was busy). For pages 10–19 it replied "nothing to read" within seconds, and the app believed it, so pages 11–18 of both (about 2,000 and 2,400 words) were never read. It also left out the page markers for pages 2–10, and the skipped-text check only looked at pages with a marker, so it missed the gap. The one page it did check, page 1, had the acknowledgements footnote, which it put back "at the end of page 1": just before the next marker, page 19, in the middle of section 2
  - Fixed: every part is checked against the PDF's paragraphs, and two or more missing pages are asked for again in halves; a single missing page goes to the skipped-text check, which now also covers pages with no marker; missing page markers are put back by matching text to pages; acknowledgements, thank-yous, correspondence lines, table notes, publishers' terms and sideways tables never count as skipped text. Scripts are written by Flash only, never Flash-Lite, and the app waits when Flash is busy. Replayed on every reading's saved Gemini output: it flags exactly the two broken readings and nothing else, and all 23 restored markers land on the right page. When Google answers "deadline expired" (it did on the table-heavy pages 19–28 for 18 minutes), the part is asked for again in halves instead of waiting
  - Rewritten "Climate Risk Disclosure" with Flash on 15 Nov: every page from 1 to 29 is there (checked against every paragraph of the PDF), the acknowledgements are gone, and half its paragraphs match the paper at least 96% word for word, so nothing was summarised (1:09:23, was 1:03:33). "Three Decades" was rewritten on 16 Nov by 3.5 Flash (3.8 Flash was overloaded all morning): all of pages 1–17 are there, median 94% word for word, 43:09 (was 23:07)
  - Scripts now use Gemini 3.5 Flash, with 3.8 Flash as the backup: 3.5 does the job just as well and is busy less often. It shares its free allowance with the Ask tab (capped at 50 questions a day). The check also skips university repository cover notices and author bios (anything with an email address)
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
- [x] A word hyphenated across a page break is rejoined without its hyphen, so "low-beta" split there would become "lowbeta"
- [x] Sentences split in two across a page break: "…particularly those who are looking for" then a new paragraph "stable, long-term exposure to climate and nature-related assets." That gives a pause in the audio and two highlights instead of one sentence. Cause: Gemini sometimes puts a blank line plus the page marker in the middle of a sentence (the code only fixes markers that sit inside a paragraph with no blank line), and the break between 10-page chunks can do the same. Fix: when a paragraph doesn't end with . ? ! : or a closing quote and the next text (past any page marker) starts with a lower-case letter, join the two and move the marker after the paragraph; do it both in script.py and as a safety net in segments.py

## Study aids
- [ ] Notes and highlights for the important sections: key arguments, definitions and findings marked in the player and exported as notes. Deciding what's important is the hard part; a first version could have Gemini mark key sentences when it writes the script
- [ ] A 60-second "here's the argument" intro before the reading starts
- [ ] A short recap at the end of each section
- [ ] 3–5 recall questions at the end
- [x] Reading chatbot: ask about what you're reading or listening to (spec below). First version built: the Ask tab, with your place or selection marked, quick buttons, page links, Copy, saved chats, its own models (3.5 Flash, then Flash-Lite) and a daily cap with a counter. Quality-tested on the Bonds reading (Flash-Lite good on quick questions, 3.5 Flash close to Claude on harder ones)
- [ ] Ask: try it on real study sessions and tune the instructions and buttons; see whether the 50-a-day cap and the 45-second switch to Flash-Lite feel right

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

## Library
- [x] **Retry by itself when Gemini is busy, and spend as few requests as possible.** On 17 Nov every free Gemini model was overloaded all morning, so papers kept stopping with "press Retry". Then Google showed that 3.5 and 3.8 Flash each get only **20 free requests a day**, and both were used up with only 2 parts written: Google seems to count requests it turns away ("high demand") against the limit, and the app asked each busy model up to 10 times per part (twice per go, then after 1, 2 and 4 minutes, then the backup), plus my test questions. Now:
  - Each model is asked once per part. Busy: the next model; none answers: the paper waits, and the card says when it tries again by itself (15 min, then 30, then hourly, for up to 12 hours). Each try first asks one model for a one-word reply, a different model each time, and one reply covers every waiting paper
  - Used up for the day: the paper waits for the reset (9:00 here) and carries on by itself, instead of "press Retry later". If the reset is late, it looks again every 15 minutes
  - Backups are now 3.8, 3.7 and 3.6 Flash, each with its own 20 a day (80 in all instead of 40). 3.6 and 3.7 haven't written a script for us yet: check the first one they do against the PDF
  - "Deadline expired" splits a part into halves down to 2 pages (not 1), and remembers which parts were too slow, so a later try doesn't ask for them again
  - **Try now** skips the wait; waiting papers can be deleted; a restart carries on waiting. `audiobook add` and `rebuild` don't wait: they stop with the reason
  - Tested with a faked Gemini on a spare copy: busy with the slowing waits, the models taking turns, used up until the reset, Try now while used up, a busy reset, answering again, the 12-hour limit, deleting, a restart and several papers at once. And each request type on its own: one request per busy model, the per-minute limit, and slow parts remembered
- [ ] **The Ask tab shares 3.5 Flash's 20 a day with scripts.** Each question uses a request a script could have had. Consider starting Ask on a model scripts don't use (Flash-Lite answered quick questions well), and lowering the 50-a-day cap, which is above what 3.5 Flash allows anyway
- [ ] **Cancel a reading while it's processing, and get rid of it.** Today the bin button is hidden while a reading is "Writing the script" or "Recording", and deleting it then is refused ("It's being processed right now"), so a long reading added by mistake has to run to the end. Add a **Cancel** button on the card while it's processing:
  - Stop at the next safe point: after the current sentence when recording, or after the current Gemini request when writing the script. Release the reading's lock and clear the half-finished recording files
  - Then offer **Move to Trash** (like the bin button), or **Keep it** so Retry can carry on later. Gemini parts already written are saved, so a retry doesn't spend those free requests again
  - Works for readings waiting in the queue too (those can already be deleted, but they should say "Cancel" there as well)
  - From the terminal: Ctrl+C on `audiobook add` should leave the reading in the same cancelled state, not half-processed

## Recording: speed and memory
Measured 13 Nov 2026 on the M3 Pro (18 GB), "European Integration" (59 pages, 1313 sentences): the app's memory footprint reached **7.8 GB**, of which only about 1 GB is the app itself. The rest is GPU memory PyTorch keeps hold of: every sentence is a different length, it holds on to each chunk it has used "in case" it needs it again, so the total keeps climbing through a long reading, and it doesn't drop after recording because the voice model stays loaded until the app quits. Fine on an 18 GB Mac, but it would make a friend's 8 GB MacBook Air crawl. Speed (about 10–15 minutes for 2½–3 hours of audio) is roughly normal.

**Rule for all of these: the audio must sound exactly as it does now.** The user is very happy with the current output. Anything that could change the sound gets an A/B listening test on the same reading first, and is kept only if it sounds as good or better. `uv run python scripts/compare_audio.py <reading>` records the same opening with main and the current code: a normal pair to listen to, and a pair with the voice's randomness pinned down that it checks sample by sample.

Do the first two together; they're small and make the biggest difference to memory:
- [x] **Clear the GPU memory every 50 sentences or so** (`torch.mps.empty_cache()`), so memory stays roughly flat at about 1.5–2.5 GB instead of climbing. *Sound: unchanged.* It only releases memory that's no longer in use; the voice model does exactly the same work.
- [x] **Unload the voice model when recording finishes,** so the app drops back to a few hundred MB while you're just listening or reading. *Sound: unchanged.* The next recording reloads it, which takes about 10 seconds.
  - Done and measured on the Bonds reading (280 sentences): memory stayed flat at 2.9–3.7 GB instead of climbing from 2.3 to 6.0 GB, and dropped to about 1.3 GB once recording finished (it used to stay at 6 GB). Audio checked: with the voice model's random noise fixed to the same seed, old and new code produce identical samples, including across the memory clean-ups. Without a fixed seed even the old code differs run to run, which is the voice's built-in randomness, not this change
- [ ] **Faster recording:** record a whole paragraph at once instead of each sentence, and take the sentence timings from the voice model's word timings, for fewer GPU calls and probably about 1.5–2× faster. *Sound: could change, so test first.* The voice would flow across sentences instead of pausing for the fixed 0.25 s gaps, and the highlight timing would come from the model rather than from exact sentence boundaries. Compare the same reading both ways (pauses, rhythm, highlight accuracy) and only switch if it's as good or better; otherwise keep sentence by sentence
- [x] **A time estimate on the card,** such as "about 6 min left". *Sound: unchanged*
- [ ] **Pick up where it stopped:** if recording is interrupted, continue from the last finished sentence instead of starting again (save each finished sentence's audio as it goes). *Sound: unchanged.* Check a resumed recording matches an uninterrupted one
- [ ] **Start listening sooner:** make the first section playable while the rest is still recording, so you're listening within a minute. The biggest change of these. *Sound: unchanged*, but the player has to cope with a recording that's still growing

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
