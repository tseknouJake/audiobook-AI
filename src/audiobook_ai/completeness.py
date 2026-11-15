"""Find paragraphs of the PDF that the listening script left out by mistake.

Gemini sometimes skips a boxed case study or sidebar. This compares every
paragraph of the PDF (from PyMuPDF's layout model, which reads columns in the
right order) with the script and reports the ones that are missing.

Plenty of text is meant to be missing, so these are never reported:
- pages Gemini left out entirely (disclaimers, reference lists), unless
  dropped_pages() found they hold real paragraphs
- everything from the script's Appendix heading on (appendices are skipped)
- footnotes, "Source:" and "Note:" lines, legal boilerplate, acknowledgements,
  short fragments
- anything the layout model calls a table, caption, list item or footnote

It also puts back page markers Gemini leaves out (add_missing_markers), since
the check, the page-following side pane and re-added text all rely on them.
"""

import json
import re
from pathlib import Path

import pymupdf

from .segments import PAGE_MARKER

MIN_WORDS = 40      # shorter blocks are usually labels, captions or fragments
KEPT_BELOW = 0.2    # under 20% of its 4-word phrases in the script = missing
DROPPED_WORDS = 150  # a page missing at least this many words of paragraphs,
DROPPED_SHARE = 0.5  # and at least half of them, was left out as a whole

_SKIP_START = re.compile(
    r"^\s*(?:[*†‡§]|\d{1,3}\s?[A-Z]|\(\d+\)|Sources?\b|Notes?\b|Methodology\b|Disclaimer"
    r"|This (?:table|figure|exhibit|chart|graph)\b)", re.I)  # the last: notes under a table
_BOILERPLATE = (
    "there is no guarantee", "the information above", "information contained", "not intended",
    "investment advice", "past performance", "this material", "this document", "should not be relied",
    "risk management processes", "is issued by", "any opinions", "does not constitute",
    "subject to change", "third party sources", "third-party sources", "for professional",
    "qualified investors", "all rights reserved", "copyright", "not be reproduced", "no part of this",
    # acknowledgements, funding and correspondence footnotes
    "we thank", "we are grateful", "we gratefully acknowledge", "acknowledgement", "acknowledgment",
    "research assistance", "helpful comments", "constructive comments", "useful comments",
    "send correspondence", "corresponding author", "financial support", "the views expressed",
    # publishers' cover pages and terms of use
    "may be used only for", "does not warrant", "terms and conditions", "terms of use", "jstor",
)


def load_layout(pdf_path: Path, cache: Path) -> dict:
    """PyMuPDF's layout analysis of the whole PDF, cached as JSON (a few seconds to make)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import pymupdf4llm

    with pymupdf.open(pdf_path) as doc:
        data = json.loads(pymupdf4llm.to_json(doc, use_ocr=False, show_progress=False))
    slim = {"pages": [{"page_number": p["page_number"], "boxes": [
        {k: b[k] for k in ("boxclass", "x0", "y0", "x1", "y1")} for b in p["boxes"]]} for p in data["pages"]]}
    cache.write_text(json.dumps(slim), encoding="utf-8")
    return slim


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _shingles(text: str) -> set:
    words = _words(text)
    return {tuple(words[i:i + 4]) for i in range(len(words) - 3)}


def _fraction_kept(text: str, shingles: set) -> float:
    words = _words(text)
    grams = [tuple(words[i:i + 4]) for i in range(len(words) - 3)]
    return sum(g in shingles for g in grams) / max(1, len(grams))


def _script_pages(script: str) -> tuple[set[int], int | None]:
    """Pages the script has text for, and the page its Appendix starts on."""
    pages, page, appendix = set(), None, None
    for line in script.splitlines():
        marker = PAGE_MARKER.fullmatch(line.strip())
        if marker:
            page = int(marker.group(1))
        elif line.strip() and page:
            pages.add(page)
            if appendix is None and re.match(r"#+\s*(appendi|annex)", line.strip(), re.I):
                appendix = page
    return pages, appendix


def _box_text(page, box) -> str:
    rect = pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])
    return " ".join(page.get_text("text", clip=rect, sort=True).split())


def _paragraphs(page, boxes):
    """(index, text) of the page's paragraphs that belong in a script: body text,
    without footnotes, notes, boilerplate or short fragments."""
    for i, box in enumerate(boxes):
        if box["boxclass"] != "text":
            continue
        text = _box_text(page, box)
        lowered = text.lower()
        if (len(_words(text)) < MIN_WORDS or _SKIP_START.match(text)
                or any(phrase in lowered for phrase in _BOILERPLATE) or _sideways(page, box)):
            continue
        yield i, text


def _sideways(page, box) -> bool:
    """A table printed sideways: its text comes out as word salad, and isn't prose."""
    rect = pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])
    lines = [line for block in page.get_text("dict", clip=rect)["blocks"] for line in block.get("lines", [])]
    upright = sum(abs(line["dir"][0]) > 0.9 for line in lines)
    return bool(lines) and upright < len(lines) / 2


def find_missing(pdf_path: Path, layout: dict, script: str, extra_pages=()) -> list[dict]:
    """Paragraphs left out by mistake: [{"page", "text", "heading"}] in reading order.
    `heading` is the box's own title when that is missing from the script too.
    Only pages the script has text for are checked, plus `extra_pages`."""
    shingles = _shingles(script)
    flat_script = " ".join(_words(script))
    pages_read, appendix = _script_pages(script)
    pages_read |= set(extra_pages)
    missing = []
    with pymupdf.open(pdf_path) as doc:
        for page_data in layout["pages"]:
            number = page_data["page_number"]
            if number not in pages_read or (appendix and number >= appendix):
                continue
            page = doc[number - 1]
            boxes = page_data["boxes"]
            for i, text in _paragraphs(page, boxes):
                if _fraction_kept(text, shingles) >= KEPT_BELOW:
                    continue
                heading = None
                if i and boxes[i - 1]["boxclass"] == "section-header":
                    title = _box_text(page, boxes[i - 1])
                    if title and " ".join(_words(title)) not in flat_script:
                        heading = title
                missing.append({"page": number, "text": text, "heading": heading})
    return missing


def dropped_pages(pdf_path: Path, layout: dict, script: str, pages) -> list[int]:
    """Pages among `pages` whose paragraphs are mostly missing from the script:
    Gemini left the whole page out, not just a box or two. Pages from the
    script's Appendix heading on don't count (appendices are skipped on purpose)."""
    shingles = _shingles(script)
    _, appendix = _script_pages(script)
    wanted = set(pages)
    dropped = []
    with pymupdf.open(pdf_path) as doc:
        for page_data in layout["pages"]:
            number = page_data["page_number"]
            if number not in wanted or (appendix and number >= appendix):
                continue
            total = missing = 0
            for _, text in _paragraphs(doc[number - 1], page_data["boxes"]):
                words = len(_words(text))
                total += words
                if _fraction_kept(text, shingles) < KEPT_BELOW:
                    missing += words
            if missing >= DROPPED_WORDS and missing >= DROPPED_SHARE * total:
                dropped.append(number)
    return dropped


def add_missing_markers(text: str, pdf_path: Path, start: int, end: int) -> str:
    """Put back the page markers Gemini left out of the script for pages
    start–end, by finding the page each paragraph starts on. Markers Gemini
    did write are kept as they are."""
    present = {int(n) for n in PAGE_MARKER.findall(text)}
    if not text.strip() or all(n in present for n in range(start + 1, end + 1)):
        return text
    with pymupdf.open(pdf_path) as doc:
        grams = {n: _shingles(doc[n - 1].get_text("text", sort=True)) for n in range(start, end + 1)}
    out, headings, current = [], [], start
    for block in (b.strip() for b in re.split(r"\n\s*\n", text.strip())):
        marker = PAGE_MARKER.fullmatch(block)
        if marker:
            out += headings + [block]
            headings, current = [], int(marker.group(1))
            continue
        if block.startswith("#"):
            headings.append(block)  # a heading goes with the paragraph after it
            continue
        page = _start_page(block, grams, current)
        if page > current and page not in present:
            out.append(f"<!-- page {page} -->")
            present.add(page)
            current = page
        out += headings + [block]
        headings = []
    return "\n\n".join(out + headings)


def _start_page(paragraph: str, grams: dict[int, set], current: int) -> int:
    """The page (current or later) the paragraph's opening words come from. Its
    first few words decide, since a paragraph often starts at the foot of one
    page and runs onto the next; a longer opening is the fallback for when
    Gemini reworded the start."""
    words = _words(paragraph)
    for length in (15, 40):
        opening = _shingles(" ".join(words[:length]))
        best, best_count = current, len(opening & grams.get(current, set()))
        for page in sorted(grams):
            if page > current:
                count = len(opening & grams[page])
                if count > best_count:
                    best, best_count = page, count
        if best_count >= 3:
            return best
    return current
