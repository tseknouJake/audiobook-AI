"""Turn a PDF into a listening script with Gemini.

Gemini reads the PDF pages directly, so scanned pages, equations and tables
are handled the same way as normal text. The PDF goes in chunks of a few
pages; each chunk's result is cached in the reading's `chunks/` folder, so a
retry never spends free-tier requests on pages already done.
"""

import io
import re
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from . import config

SYSTEM_PROMPT = """\
You turn pages of an academic reading into a listening script. A text-to-speech \
voice will read your script aloud to a university student, who follows along \
and takes notes, so the script must keep the author's full argument.

Keep:
- Every sentence of the main text, in the author's words and order. Do not \
summarise, shorten, paraphrase or add commentary. Small fixes so a sentence \
reads well aloud are fine.
- The title, the abstract, and the section and subsection headings.

Remove:
- Page numbers, running headers and footers, journal names, volume and issue \
numbers, copyright notices, submission dates, author affiliations, email \
addresses, URLs and DOIs.
- Footnote and endnote markers, and the notes themselves.
- Reference lists and bibliographies. Write nothing for them.
- Acknowledgements and funding statements.
- Hyphenation at line breaks: join words split across lines.

Citations:
- Delete parenthetical citations entirely, such as "(Fama and French, 1993; \
Carhart, 1997)" or "(see Smith et al., 2019, p. 45)".
- When a citation is part of the sentence, keep the names and drop the year and \
page: "Fama and French (1993) show that" becomes "Fama and French show that". \
Write "et al." as "and colleagues".

Make it easy to say aloud:
- Expand abbreviations: "e.g." to "for example", "i.e." to "that is", "cf." to \
"compare", "vs." to "versus", "approx." to "approximately", "Fig." to \
"Figure", "Eq." to "Equation".
- Write symbols as words: "%" as "percent", "β" as "beta", "α" as "alpha", \
"σ" as "sigma", "≈" as "approximately", "$2.5bn" as "2.5 billion dollars".
- Keep common acronyms as written, such as CAPM, OLS, GARCH and GDP.

Equations:
- Short maths in a sentence: say it in words, using what the paper says the \
symbols mean when that is clear ("the market's excess return" rather than \
"r sub m minus r sub f").
- Displayed equations: if short, say it in words. If long, write one sentence \
on what it expresses, starting with its number if it has one, for example \
"Equation 3 says that expected excess returns rise in proportion to beta."
- Never write LaTeX, subscripts or symbol-by-symbol notation.

Tables, figures and regression output:
- Never read cells, coefficients or axis values one by one. In their place, \
write one or two sentences on what it shows and its main finding, based only on \
what is printed, and name it so the student can find it, for example: "Table 2 \
reports regressions of monthly returns on the three factors. The market beta is \
close to one and the size factor is significant. See Table 2 for the full \
results."

Appendices: write one sentence saying what the appendix contains, then skip it.

Format:
- Use Markdown only for headings: "# " for the reading's title, "## " for \
sections, "### " for subsections. Leave section numbers out of headings.
- Plain paragraphs separated by one blank line. No bullet points, bold, \
italics, tables or code blocks.
- Output only the script: no preamble, and no notes about what you changed.
"""

NOTHING = "<<NOTHING>>"


class QuotaError(RuntimeError):
    """The free tier's limit is used up for now."""


def page_ranges(total_pages: int, per_chunk: int) -> list[tuple[int, int]]:
    """1-based inclusive page ranges. Each chunk repeats the previous chunk's
    last page, so a sentence cut off at a page break is finished in the next."""
    per_chunk = max(1, per_chunk)
    overlap = 1 if per_chunk >= 3 else 0
    ranges, start = [], 1
    while start <= total_pages:
        end = min(total_pages, start + per_chunk - 1)
        ranges.append((start, end))
        if end == total_pages:
            break
        start = end + 1 - overlap
    return ranges


def _pdf_slice(reader: PdfReader, start: int, end: int) -> bytes:
    writer = PdfWriter()
    for index in range(start - 1, end):
        writer.add_page(reader.pages[index])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _chunk_prompt(start: int, end: int, total: int, tail: str, is_last: bool, overlap: bool) -> str:
    parts = [f"These are pages {start} to {end} of a {total}-page reading."]
    if not tail:
        parts.append("Start from the beginning of the reading and include its title as a \"# \" heading.")
    else:
        repeated = f"Page {start} was also part of the earlier pages. " if overlap else ""
        parts.append(
            "The script for the earlier pages ended with:\n\n\"\"\"\n" + tail + "\n\"\"\"\n\n"
            f"{repeated}Continue from the first word after that ending. Do not repeat "
            "anything already covered and do not add a title."
        )
    if is_last:
        parts.append("This is the end of the reading.")
    else:
        parts.append(
            "If the last page ends partway through a sentence, stop at the end of the last "
            "complete sentence; the next part will continue from there."
        )
    parts.append(f"If these pages contain nothing to read aloud, such as only references, reply with exactly {NOTHING}.")
    return "\n\n".join(parts)


def _tail(script: str, chars: int = 700) -> str:
    script = script.strip()
    if len(script) <= chars:
        return script
    cut = script[-chars:]
    return cut[cut.find(" ") + 1:]


def _call_gemini(client, model: str, pdf_bytes: bytes, prompt: str, log) -> tuple[str, str]:
    """Return (text, finish_reason), retrying short outages and rate limits."""
    from google.genai import errors, types

    config_ = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
    contents = [types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), prompt]
    for attempt in range(4):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config_)
        except errors.ClientError as exc:
            message = str(exc)
            if exc.code != 429:
                raise
            if "PerDay" in message or "per day" in message.lower() or attempt == 3:
                raise QuotaError(message) from exc
            wait = 30 * (attempt + 1)
            log(f"Gemini rate limit hit; waiting {wait}s")
            time.sleep(wait)
            continue
        except errors.ServerError:
            if attempt == 3:
                raise
            log("Gemini server error; retrying")
            time.sleep(10 * (attempt + 1))
            continue
        candidate = (response.candidates or [None])[0]
        finish = str(getattr(candidate, "finish_reason", "") or "").split(".")[-1]
        return (response.text or "").strip(), finish
    raise RuntimeError("Gemini did not respond")


def _basic_clean(text: str) -> str:
    """Rough clean-up of a page's raw text, used only when Gemini refuses one."""
    text = re.sub(r"-\n(\w)", r"\1", text)
    text = re.sub(r"https?://\S+|doi:\S+", "", text)
    text = re.sub(r"\((?:see |e\.g\., )?[A-Z][^()]*?\d{4}[a-z]?(?:[^()]*?)\)", "", text)
    lines = [ln for ln in text.splitlines() if not re.fullmatch(r"\s*\d{1,4}\s*", ln)]
    text = " ".join(" ".join(lines).split())
    return text


def _script_for_range(client, reader, start, end, total, tail, is_last, overlap, models, log, warnings) -> str:
    """Script for one page range, splitting it if Gemini stops early or refuses."""
    prompt = _chunk_prompt(start, end, total, tail, is_last, overlap)
    pdf_bytes = _pdf_slice(reader, start, end)
    for i, model in enumerate(models):
        try:
            text, finish = _call_gemini(client, model, pdf_bytes, prompt, log)
            break
        except QuotaError:
            if i == len(models) - 1:
                raise
            log(f"{model} is out of free requests; switching to {models[i + 1]}")
    if finish in ("STOP", ""):
        return "" if text.strip() in ("", NOTHING) else text

    if end > start:
        log(f"Gemini stopped early on pages {start}-{end} ({finish}); trying smaller pieces")
        middle = (start + end) // 2
        first = _script_for_range(client, reader, start, middle, total, tail, False, overlap,
                                  models, log, warnings)
        second = _script_for_range(client, reader, middle + 1, end, total, _tail(first) or tail,
                                   is_last, False, models, log, warnings)
        return "\n\n".join(part for part in (first, second) if part)

    warnings.append(f"Gemini would not process page {start} ({finish}), so its raw text was used as-is.")
    return _basic_clean(reader.pages[start - 1].extract_text() or "")


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{2,}", text))


def write_script(pdf_path: Path, reading_dir: Path, progress=lambda done, total, msg: None,
                 log=print) -> dict:
    """Write reading_dir/script.md from the PDF. Returns details for meta.json."""
    from google import genai

    if not config.has_gemini_key():
        raise RuntimeError("No Gemini API key. Add GEMINI_API_KEY to the .env file (see README).")

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    ranges = page_ranges(total, config.PAGES_PER_CHUNK)
    models = [m for m in (config.GEMINI_MODEL, config.GEMINI_FALLBACK_MODEL) if m]
    client = genai.Client()
    chunk_dir = reading_dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)

    warnings: list[str] = []
    parts: list[str] = []
    tail = ""
    for n, (start, end) in enumerate(ranges, 1):
        progress(n - 1, len(ranges), f"pages {start}–{end}")
        cache = chunk_dir / f"pages_{start:03d}-{end:03d}.md"
        overlap = n > 1 and start == ranges[n - 2][1]
        if cache.exists():
            text = cache.read_text(encoding="utf-8")
        else:
            text = _script_for_range(client, reader, start, end, total, tail,
                                     n == len(ranges), overlap, models, log, warnings)
            cache.write_text(text, encoding="utf-8")
        if text.strip():
            parts.append(text.strip())
            tail = _tail(text)
    progress(len(ranges), len(ranges), "done")

    script = "\n\n".join(parts).strip() + "\n"
    (reading_dir / "script.md").write_text(script, encoding="utf-8")

    pdf_words = sum(word_count(page.extract_text() or "") for page in reader.pages)
    coverage = None
    if pdf_words > 60 * total:  # a scanned PDF has almost no extractable text
        coverage = round(word_count(script) / pdf_words, 2)
        if coverage < 0.55:
            warnings.append(
                f"The script is only {coverage:.0%} as long as the PDF's text. Gemini may have "
                "shortened parts of it; check script.md (references and tables account for some of the gap)."
            )
    title_match = re.search(r"^# (.+)$", script, re.MULTILINE)
    return {
        "pages": total,
        "coverage": coverage,
        "warnings": warnings,
        "model": config.GEMINI_MODEL,
        "script_title": title_match.group(1).strip() if title_match else None,
    }
