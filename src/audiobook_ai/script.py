"""Turn a PDF into a listening script with Gemini.

Gemini reads the PDF pages directly, so scanned pages, equations and tables
are handled the same way as normal text. The PDF goes in chunks of a few
pages; each chunk's result is cached in the reading's `chunks/` folder, so a
retry never spends free-tier requests on pages already done.
"""

import hashlib
import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from . import completeness, config, stitch
from .segments import PAGE_MARKER

MAX_RECOVERY_REQUESTS = 6  # per reading, to protect the free tier's daily limit
BUSY_WAITS = (60, 120, 240)  # seconds to wait for a busy model before giving up

SYSTEM_PROMPT = """\
You turn pages of an academic reading into a listening script. A text-to-speech \
voice will read your script aloud to a university student, who follows along \
and takes notes, so the script must keep the author's full argument.

Keep:
- Every sentence of the main text, in the author's words and order. Do not \
summarise, shorten, paraphrase or add commentary. Small fixes so a sentence \
reads well aloud are fine.
- The title, the abstract, and the section and subsection headings.
- Boxed text that is part of the content: case studies, examples, sidebars \
and callouts, even when set apart in a box, a column or a different colour. \
Give each a "### " heading (its own title if it has one) and put it after the \
paragraph it sits beside.

Remove:
- Page numbers, running headers and footers, journal names, volume and issue \
numbers, copyright notices, submission dates, author affiliations, email \
addresses, URLs and DOIs.
- Footnote and endnote markers, and the notes themselves.
- "Source:" and "Note:" lines under tables and figures.
- Legal disclaimers and "important information" pages.
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
- When the text defines the symbols ("where β_i = Cov(R_i, R_m)/Var(R_m)"), \
turn the definition into a full sentence about what the term means, for \
example "Here an asset's beta is its covariance with the market divided by \
the market's variance." Make the sentences around an equation flow as \
ordinary prose.
- Never write LaTeX, symbol-by-symbol notation, or the words "subscript", \
"sub" or "hat".

Tables, figures and regression output:
- Never read cells, coefficients or axis values one by one. In their place, \
write two or three sentences: what it shows, then its main finding (which way \
the numbers move, which results are large or significant), based only on what \
is printed, and name it so the student can find it, for example: "Table 2 \
reports regressions of monthly returns on the three factors. The market beta is \
close to one, and only the size factor is significant. See Table 2 for the \
full results." Always include the main finding; a description of the layout \
alone is not enough.

Appendices: write one sentence saying what the appendix contains, then skip it.

Format:
- Use Markdown only for headings: "# " for the reading's title, "## " for \
sections (including "## Abstract"), "### " for subsections. Leave section \
numbers out of headings.
- Plain paragraphs separated by one blank line. No bullet points, bold, \
italics, tables or code blocks.
- Page markers: before the first paragraph that starts on each page, put a \
line with only <!-- page N -->, where N counts the pages of the attached file \
from 1. Ignore the page numbers printed on the pages. Put markers only between \
paragraphs: when a paragraph runs onto the next page, finish it first. Leave \
out markers for pages with nothing to read aloud.
- Output only the script: no preamble, and no notes about what you changed.
"""

NOTHING = "<<NOTHING>>"
REQUEST_TIMEOUT_MS = 300_000  # a 10-page chunk can take a couple of minutes


class QuotaError(RuntimeError):
    """The free tier's limit is used up for now."""


class ModelUnavailable(RuntimeError):
    """Google keeps failing to serve the model (overloaded or timing out)."""


class TooSlow(ModelUnavailable):
    """Google's time limit ran out before the answer was ready ("deadline
    expired"). Asking again for the same pages usually fails the same way;
    fewer pages at a time usually works."""


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
        repeated = (f"Page {start} was the last page of the earlier part, so it is mostly or wholly "
                    f"covered already. Do not start page {start} again from the top: begin with the very "
                    f"next sentence after that ending, which may be further down page {start} or on "
                    f"page {start + 1}. ") if overlap else ""
        parts.append(
            "The script so far ends with:\n\n\"\"\"\n" + tail + "\n\"\"\"\n\n"
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


def _absolute_pages(text: str, start: int, end: int, reader: PdfReader | None = None) -> str:
    """Gemini numbers the pages of the slice it was sent from 1; turn those into
    the PDF's own page numbers, then tidy where the markers sit."""
    local = [int(n) for n in PAGE_MARKER.findall(text)]
    # If Gemini used the printed page numbers instead, count from its first marker.
    offset = start - 1 if not local or max(local) <= end - start + 1 else start - min(local)

    def convert(match):
        page = min(max(int(match.group(1)) + offset, start), end)
        return f"<!-- page {page} -->"

    text = PAGE_MARKER.sub(convert, text).strip()
    if not text.startswith("<!--"):
        text = f"<!-- page {start} -->\n\n{text}"  # whatever comes first is on the first page
    return _markers_between_paragraphs(text, reader)


def _markers_between_paragraphs(text: str, reader: PdfReader | None) -> str:
    """Move markers Gemini put inside a paragraph (it often does at a page break)
    to just after it, rejoining a word split across the break ("pat-tern")."""
    blocks = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n")]
        moved = []
        k = 1
        while k < len(lines):
            marker = PAGE_MARKER.fullmatch(lines[k].strip())
            if not marker:
                k += 1
                continue
            moved.append(lines.pop(k))
            if k < len(lines) and k > 0:
                lines[k - 1:k + 1] = [stitch.join_words(lines[k - 1], lines[k], document_text(reader))]
        blocks.append("\n".join(lines))
        blocks.extend(moved)
    return "\n\n".join(b for b in blocks if b.strip())


def document_text(reader: PdfReader | None) -> str | None:
    """The PDF's raw text, used to decide whether a word split at a break keeps its hyphen."""
    if reader is None:
        return None
    if not hasattr(reader, "_audiobook_text"):
        reader._audiobook_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return reader._audiobook_text


def _tail(script: str, chars: int = 700) -> str:
    script = PAGE_MARKER.sub("", script).strip()
    if len(script) <= chars:
        return script
    cut = script[-chars:]
    return cut[cut.find(" ") + 1:]


def make_client():
    """A Gemini client with a time limit per request. The SDK's own retries are
    off so that _call_gemini decides when to give up on a model."""
    from google import genai
    from google.genai import types

    return genai.Client(http_options=types.HttpOptions(
        timeout=REQUEST_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))


def _pdf_part(pdf_bytes: bytes):
    from google.genai import types

    return types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")


def _generate(client, models: list[str], contents: list, log, can_split: bool = False) -> tuple[str, str]:
    """Ask the first model in `models` that works. A model that is out of free
    requests or not responding is dropped from the (shared) list, so the rest
    of the reading skips it too. When the last one is busy, wait for it (a few
    minutes in all) rather than give up straight away. With `can_split`, running
    out of time raises TooSlow at once, so the caller can ask for fewer pages."""
    waits = list(BUSY_WAITS)
    while True:
        model = models[0]
        try:
            return _call_gemini(client, model, contents, log)
        except (QuotaError, ModelUnavailable) as exc:
            if isinstance(exc, TooSlow) and can_split:
                raise
            if len(models) > 1:
                reason = "is out of free requests" if isinstance(exc, QuotaError) else "isn't responding"
                log(f"{model} {reason}; using {models[1]} for the rest of this reading")
                models.pop(0)
                continue
            if isinstance(exc, ModelUnavailable) and waits:
                wait = waits.pop(0)
                log(f"{model} is busy; trying again in {wait // 60} min")
                time.sleep(wait)
                continue
            raise


def _call_gemini(client, model: str, contents: list, log) -> tuple[str, str]:
    """Return (text, finish_reason).

    Raises QuotaError when the model's free limit is used up and
    ModelUnavailable when it keeps failing on Google's side (overloaded,
    timing out), so the caller can move on to the fallback model.
    """
    from google.genai import errors, types

    config_ = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
    rate_limited = server_failures = 0
    while True:
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config_)
        except errors.ClientError as exc:
            message = str(exc)
            if exc.code != 429:
                raise
            rate_limited += 1
            if "PerDay" in message or "per day" in message.lower() or rate_limited > 2:
                raise QuotaError(message) from exc
            wait = 30 * rate_limited
            log(f"{model}: per-minute limit hit; waiting {wait}s")
            time.sleep(wait)
            continue
        except Exception as exc:  # 5xx from Google, or the request timing out
            timed_out = "timeout" in type(exc).__name__.lower() or getattr(exc, "code", None) == 504
            if not (isinstance(exc, errors.ServerError) or timed_out):
                raise
            if timed_out:
                raise TooSlow(f"{model}: {exc}") from exc
            server_failures += 1
            if server_failures >= 2:
                raise ModelUnavailable(f"{model}: {exc}") from exc
            log(f"{model}: unavailable ({str(exc)[:80]}); trying once more")
            time.sleep(5)
            continue
        candidate = (response.candidates or [None])[0]
        finish = str(getattr(candidate, "finish_reason", "") or "").split(".")[-1]
        return (response.text or "").strip(), finish


_YEAR = r"(?:1[89]\d{2}|20\d{2})[a-z]?"
_PAGES = r"(?:pp?\.|pages?)\s*\d+(?:\s*[–-]\s*\d+)?"
# One citation: names, then a year and/or page. "Fama and French, 1993",
# "Smith et al., 2019, p. 4", "Baker and colleagues, p. 42", "van Dijk 2011".
_NOT_NAMES = (r"(?!(?:January|February|March|April|May|June|July|August|September|October"
              r"|November|December|Spring|Summer|Autumn|Fall|Winter|Q[1-4])\b)")
_NAME = rf"(?:(?:van|von|de|der|den|du|le|la) )?{_NOT_NAMES}[A-Z][A-Za-z'’\-]+"
_CITE = (rf"(?:see |see also |e\.g\.,? |cf\.? |for example,? )?{_NAME}"
         rf"(?:(?:,|\sand|\s&)?\s+{_NAME})*(?: et al\.| and colleagues)?,?\s*"
         rf"(?:{_YEAR}(?:,\s*{_PAGES})?|{_PAGES})")
# A bracket holding only citations: "(Fama and French, 1993; Carhart, 1997)"
_PAREN_CITATION = re.compile(rf"\s*\({_CITE}(?:\s*[;,]\s*{_CITE})*\)")
# "Sharpe (1964)" -> "Sharpe"
_YEAR_ONLY = re.compile(rf"\s*\({_YEAR}(?:,\s*{_PAGES})?\)")


def strip_citations(text: str) -> str:
    """Safety net for citations Gemini leaves in (smaller models sometimes do).
    Parentheses without a name, like "(1965–2020)", are left alone."""
    text = _PAREN_CITATION.sub("", text)
    text = _YEAR_ONLY.sub("", text)
    return re.sub(r" +([.,;:])", r"\1", text)


def strip_heading_numbers(text: str) -> str:
    """'## 3.2 Data' -> '## Data' (Gemini is asked to do this but doesn't always)."""
    return re.sub(r"^(#{1,6}\s+)(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVX]+\.|[A-Z]\.)\s+(?=\S)", r"\1", text,
                  flags=re.MULTILINE)


def _basic_clean(text: str) -> str:
    """Rough clean-up of a page's raw text, used only when Gemini refuses one."""
    text = re.sub(r"-\n(\w)", r"\1", text)
    text = re.sub(r"https?://\S+|doi:\S+", "", text)
    text = re.sub(r"\((?:see |e\.g\., )?[A-Z][^()]*?\d{4}[a-z]?(?:[^()]*?)\)", "", text)
    lines = [ln for ln in text.splitlines() if not re.fullmatch(r"\s*\d{1,4}\s*", ln)]
    text = " ".join(" ".join(lines).split())
    return text


@dataclass
class _Job:
    """What every request for one reading needs."""
    client: object
    reader: PdfReader
    pdf_path: Path
    layout: dict
    chunk_dir: Path
    models: list[str]
    log: object
    warnings: list[str]
    left_out: list[int] = field(default_factory=list)  # single pages for the skipped-text check


def _script_for_range(job: _Job, start: int, end: int, before: str, is_last: bool, overlap: bool) -> str:
    """Script for pages start–end, given the script so far (`before`).

    Every answer is saved in chunks/ and checked against the PDF's paragraphs.
    When Gemini stops early, refuses, or leaves out two or more pages that have
    real paragraphs on them, the range is asked for again in two halves (each
    checked the same way). A single page left out goes to the skipped-text
    check at the end, which costs only one request.
    """
    cache = job.chunk_dir / f"pages_{start:03d}-{end:03d}.md"
    if cache.exists():
        text, finish = _tidy_cached(cache.read_text(encoding="utf-8"), start, end, job), "STOP"
    else:
        prompt = _chunk_prompt(start, end, len(job.reader.pages), _tail(before), is_last, overlap)
        try:
            text, finish = _generate(job.client, job.models, [_pdf_part(_pdf_slice(job.reader, start, end)), prompt],
                                     job.log, can_split=end > start)
        except TooSlow:
            text, finish = "", "TOO_SLOW"
        if finish in ("STOP", ""):
            body = text.replace(NOTHING, "").strip()
            text = _absolute_pages(body, start, end, job.reader) if PAGE_MARKER.sub("", body).strip() else ""
            text = completeness.add_missing_markers(text, job.pdf_path, start, end)
            cache.write_text(text, encoding="utf-8")

    if finish in ("STOP", ""):
        dropped = completeness.dropped_pages(job.pdf_path, job.layout, f"{before}\n\n{text}",
                                             range(start, end + 1))
        if len(dropped) < 2 or start == end:
            job.left_out += dropped
            return text
        job.log(f"Gemini left out {_pages_phrase(dropped)}; asking for pages {start}-{end} again in two halves")
    elif start == end:
        job.warnings.append(f"Gemini would not process page {start} ({finish}), so its raw text was used as-is.")
        raw = _basic_clean(job.reader.pages[start - 1].extract_text() or "")
        return f"<!-- page {start} -->\n\n{raw}" if raw else ""
    elif finish == "TOO_SLOW":
        job.log(f"Gemini ran out of time on pages {start}-{end}; asking in two halves")
    else:
        job.log(f"Gemini stopped early on pages {start}-{end} ({finish}); trying smaller pieces")

    middle = (start + end) // 2
    first = _script_for_range(job, start, middle, before, False, overlap)
    second = _script_for_range(job, middle + 1, end, f"{before}\n\n{first}", is_last, False)
    text = "\n\n".join(part for part in (first, second) if part)
    cache.write_text(text, encoding="utf-8")  # a later run reuses this instead of asking again
    return text


def _tidy_cached(text: str, start: int, end: int, job: _Job) -> str:
    """A saved answer, re-tidied in case it was saved by an older version."""
    text = text.strip()
    if not text:
        return ""
    if not text.startswith("<!--"):
        text = f"<!-- page {start} -->\n\n{text}"
    text = _markers_between_paragraphs(text, job.reader)
    return completeness.add_missing_markers(text, job.pdf_path, start, end)


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{2,}", text))


def _pages_phrase(pages) -> str:
    pages = sorted(set(pages))
    if len(pages) == 1:
        return f"page {pages[0]}"
    return "pages " + ", ".join(map(str, pages[:-1])) + f" and {pages[-1]}"


def _recovery_prompt(page: int, passage: str) -> str:
    return (
        f"This text from page {page} of the reading was left out of the listening script. It may be "
        "a boxed section, such as a case study, example or sidebar, or part of the main text. Turn it "
        "into listening "
        "script, following the same rules. If it starts with its own title, make that a \"### \" "
        "heading. Output only the script for this text: no page markers, and nothing else from "
        f"the reading.\n\n\"\"\"\n{passage}\n\"\"\""
    )


def _insert_on_page(script: str, page: int, addition: str) -> str:
    """Put `addition` at the end of `page`'s text, just before the next page's
    marker, but ahead of any headings there that only start the next section."""
    end = len(script)
    for match in PAGE_MARKER.finditer(script):
        if int(match.group(1)) > page:
            end = match.start()
            break
    before, after = script[:end].rstrip(), script[end:]
    trailing = re.search(r"(?:\n\s*#{1,6} [^\n]*\s*)+$", before)
    if trailing:
        before, after = before[:trailing.start()].rstrip(), before[trailing.start():].strip() + "\n\n" + after
    return f"{before}\n\n{addition}\n\n{after}".rstrip() + "\n"


def _recover_missing(job: _Job, script: str):
    """Find paragraphs Gemini skipped by mistake, ask for just those, and add
    them on their page. Returns (script, pages added to, pages still missing)."""
    client, models, pdf_path, layout, log = job.client, job.models, job.pdf_path, job.layout, job.log
    reading_dir = job.chunk_dir.parent
    missing = completeness.find_missing(pdf_path, layout, script, job.left_out)
    by_page: dict[int, list[dict]] = {}
    for block in missing:
        by_page.setdefault(block["page"], []).append(block)

    added, requests = [], 0
    for page, blocks in sorted(by_page.items()):
        passage = "\n\n".join(
            (f"{b['heading']}\n\n" if b["heading"] else "") + b["text"] for b in blocks)
        key = hashlib.sha1(passage.encode("utf-8")).hexdigest()[:10]
        cache = reading_dir / "chunks" / f"recovered_p{page:03d}_{key}.md"
        if cache.exists():
            text = cache.read_text(encoding="utf-8").strip()
        elif requests < MAX_RECOVERY_REQUESTS:
            requests += 1
            log(f"Text on page {page} was skipped; asking Gemini for it")
            text, finish = _generate(client, models, [_recovery_prompt(page, passage)], log)
            text = PAGE_MARKER.sub("", text.replace(NOTHING, "")).strip() if finish in ("STOP", "") else ""
            cache.write_text(text, encoding="utf-8")  # cached even when empty, so it isn't asked again
        else:
            continue
        if text:
            if page not in {int(n) for n in PAGE_MARKER.findall(script)}:
                text = f"<!-- page {page} -->\n\n{text}"  # a page Gemini left out altogether
            script = _insert_on_page(script, page, text)
            added.append(page)

    still = sorted({b["page"] for b in completeness.find_missing(pdf_path, layout, script, job.left_out)})
    return script, added, still


def write_script(pdf_path: Path, reading_dir: Path, progress=lambda done, total, msg: None,
                 log=print) -> dict:
    """Write reading_dir/script.md from the PDF. Returns details for meta.json."""
    if not config.has_gemini_key():
        raise RuntimeError("No Gemini API key. Add GEMINI_API_KEY to the .env file (see README).")

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    ranges = page_ranges(total, config.PAGES_PER_CHUNK)
    models = list(config.GEMINI_MODELS)
    client = make_client()
    chunk_dir = reading_dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)

    warnings: list[str] = []
    parts: list[str] = []
    repeats_removed: list[int] = []
    # The PDF's paragraphs, from PyMuPDF's layout model on this Mac (no Gemini
    # requests): every answer is checked against them.
    layout = completeness.load_layout(pdf_path, reading_dir / "layout.json")
    job = _Job(client, reader, pdf_path, layout, chunk_dir, models, log, warnings)
    # Each finished part is saved as soon as it arrives, so after a crash, a
    # closed app or a used-up free limit, this loop picks up from the saved parts
    # and the next request gets the same "the script so far ends with" context.
    for n, (start, end) in enumerate(ranges, 1):
        progress(n - 1, len(ranges), f"pages {start}–{end}")
        overlap = n > 1 and start == ranges[n - 2][1]
        text = _script_for_range(job, start, end, "\n\n".join(parts), n == len(ranges), overlap)
        if text.strip() and parts:
            # Gemini sometimes starts the shared page again: drop what it repeats.
            text, removed = stitch.drop_repeated_start("\n\n".join(parts), text)
            if removed >= 15:
                repeats_removed.append(start)
                log(f"Removed {removed} repeated words at the start of pages {start}–{end}")
        if text.strip():
            parts.append(text.strip())
    progress(len(ranges), len(ranges), "checking nothing was skipped")
    script = stitch.join_split_sentences("\n\n".join(parts).strip(), document_text(reader))
    if repeats_removed:
        warnings.append(f"Gemini went over {_pages_phrase(repeats_removed)} twice; the repeat was removed.")
    try:
        script, added, still_missing = _recover_missing(job, script)
    except (QuotaError, ModelUnavailable):
        added, still_missing = [], []
        warnings.append("Couldn't check for skipped text because Gemini's free limit ran out; "
                        "Re-record later to run the check.")
    if added:
        warnings.append(f"Gemini had skipped some text on {_pages_phrase(added)}. "
                        "It was sent back and added to the script.")
    if still_missing:
        warnings.append(f"Some text on {_pages_phrase(still_missing)} may still be missing from the "
                        "script. Compare with those pages in the Pages pane.")

    script = strip_heading_numbers(strip_citations(script)) + "\n"
    repeated = stitch.find_repeats(script)
    if repeated:
        warnings.append(f"Some text on {_pages_phrase(repeated)} may be repeated in the script. "
                        "Check those pages (the script is editable; then press Re-record).")
    (reading_dir / "script.md").write_text(script, encoding="utf-8")

    pdf_words = sum(word_count(page.extract_text() or "") for page in reader.pages)
    # For reference only: disclaimers, references and tables are left out on
    # purpose, so a low number doesn't mean text is missing (the check above does that).
    coverage = round(word_count(script) / pdf_words, 2) if pdf_words > 60 * total else None
    title_match = re.search(r"^# (.+)$", script, re.MULTILINE)
    return {
        "pages": total,
        "coverage": coverage,
        "warnings": warnings,
        "model": models[0],  # the fallback, if the main model had to be skipped
        "script_title": title_match.group(1).strip() if title_match else None,
    }
