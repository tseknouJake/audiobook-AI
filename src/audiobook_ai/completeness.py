"""Find paragraphs of the PDF that the listening script left out by mistake.

Gemini sometimes skips a boxed case study or sidebar. This compares every
paragraph of the PDF (from PyMuPDF's layout model, which reads columns in the
right order) with the script and reports the ones that are missing.

Plenty of text is meant to be missing, so these are never reported:
- pages Gemini left out entirely (disclaimers, reference lists)
- everything from the script's Appendix heading on (appendices are skipped)
- footnotes, "Source:" and "Note:" lines, legal boilerplate, short fragments
- anything the layout model calls a table, caption, list item or footnote
"""

import json
import re
from pathlib import Path

import pymupdf

from .segments import PAGE_MARKER

MIN_WORDS = 40      # shorter blocks are usually labels, captions or fragments
KEPT_BELOW = 0.2    # under 20% of its 4-word phrases in the script = missing

_SKIP_START = re.compile(
    r"^\s*(?:[*†‡§]|\d{1,3}\s?[A-Z]|\(\d+\)|Sources?\b|Notes?\b|Methodology\b|Disclaimer)", re.I)
_BOILERPLATE = (
    "there is no guarantee", "the information above", "information contained", "not intended",
    "investment advice", "past performance", "this material", "this document", "should not be relied",
    "risk management processes", "is issued by", "any opinions", "does not constitute",
    "subject to change", "third party sources", "third-party sources", "for professional",
    "qualified investors", "all rights reserved", "copyright", "not be reproduced", "no part of this",
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


def find_missing(pdf_path: Path, layout: dict, script: str) -> list[dict]:
    """Paragraphs left out by mistake: [{"page", "text", "heading"}] in reading order.
    `heading` is the box's own title when that is missing from the script too."""
    words = _words(script)
    shingles = {tuple(words[i:i + 4]) for i in range(len(words) - 3)}
    flat_script = " ".join(words)
    pages_read, appendix = _script_pages(script)
    missing = []
    with pymupdf.open(pdf_path) as doc:
        for page_data in layout["pages"]:
            number = page_data["page_number"]
            if number not in pages_read or (appendix and number >= appendix):
                continue
            page = doc[number - 1]
            boxes = page_data["boxes"]

            def text_of(box) -> str:
                rect = pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])
                return " ".join(page.get_text("text", clip=rect, sort=True).split())

            for i, box in enumerate(boxes):
                if box["boxclass"] != "text":
                    continue
                text = text_of(box)
                lowered = text.lower()
                if (len(_words(text)) < MIN_WORDS or _SKIP_START.match(text)
                        or any(phrase in lowered for phrase in _BOILERPLATE)
                        or _fraction_kept(text, shingles) >= KEPT_BELOW):
                    continue
                heading = None
                if i and boxes[i - 1]["boxclass"] == "section-header":
                    title = text_of(boxes[i - 1])
                    if title and " ".join(_words(title)) not in flat_script:
                        heading = title
                missing.append({"page": number, "text": text, "heading": heading})
    return missing
