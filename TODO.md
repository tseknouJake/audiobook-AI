# TODO

## Test with real readings first
- [x] Add a Gemini API key and test with a made-up 2-page paper (citations, equation, table, references all handled)
- [ ] Run a few real readings (a text PDF, a scanned chapter, an equation-heavy paper)
- [ ] Compare Flash with Flash-Lite on the same reading once Flash isn't overloaded; Flash-Lite sometimes leaves citations in (a safety net now strips them)
- [ ] Flash-Lite skipped most of a case-study box (page 9) and part of a column page (page 6) in "Bonds and (Planetary) Boundaries 2.0". Fix: tell Gemini to keep boxes, sidebars and case studies; check each page's wording against the PDF and re-ask for pages that came back nearly empty; make the warning name those pages instead of a misleading overall percentage (disclaimers drag it down)
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

## Study aids
- [ ] Notes and highlights for the important sections: key arguments, definitions and findings marked in the player and exported as notes. Deciding what's important is the hard part; a first version could have Gemini mark key sentences when it writes the script
- [ ] A 60-second "here's the argument" intro before the reading starts
- [ ] A short recap at the end of each section
- [ ] 3–5 recall questions at the end

## Inputs and outputs
- [ ] Other inputs: EPUB, Word, web articles
- [ ] Private podcast feed for listening away from the desk
