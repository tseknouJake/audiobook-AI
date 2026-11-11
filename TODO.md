# TODO

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

## Study aids
- [ ] Notes and highlights for the important sections: key arguments, definitions and findings marked in the player and exported as notes. Deciding what's important is the hard part; a first version could have Gemini mark key sentences when it writes the script
- [ ] A 60-second "here's the argument" intro before the reading starts
- [ ] A short recap at the end of each section
- [ ] 3–5 recall questions at the end

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
