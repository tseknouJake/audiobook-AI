"""Split a listening script (Markdown) into headings and sentences."""

import re
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import pysbd

_segmenter = pysbd.Segmenter(language="en", clean=False)
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
PAGE_MARKER = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)
_PAGE = PAGE_MARKER


def _clean(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text)  # comments are never read aloud
    text = re.sub(r"[*_`]+", "", text)  # stray Markdown emphasis
    return re.sub(r"\s+", " ", text).strip()


def parse_script(markdown: str) -> list[dict]:
    """Return segments in reading order.

    Each is {"kind": "heading", "level", "text", "page"} or
    {"kind": "sentence", "para", "text", "page"}, where `para` numbers the
    paragraphs and `page` is the PDF page from the script's <!-- page N -->
    markers (None before the first marker, or for scripts without them).
    """
    from .stitch import join_words

    # 1. Headings and whole paragraphs, each with the page it starts on.
    items: list[dict] = []
    page = None
    next_page = None  # a marker inside a paragraph applies once it ends
    lines: list[str] = []

    def flush() -> None:
        nonlocal page, next_page
        text = _clean(" ".join(lines))
        lines.clear()
        if text:
            previous = items[-1] if items else None
            # A sentence split across a page break: "…looking for" / "stable, long-term…"
            if (previous and previous["kind"] == "para" and re.match(r"[a-z]", text)
                    and not re.search(r"""[.?!:;…"”’)\]]$""", previous["text"])):
                previous["text"] = join_words(previous["text"], text, None)
            else:
                items.append({"kind": "para", "text": text, "page": page})
        if next_page is not None:
            page, next_page = next_page, None

    for raw in markdown.splitlines():
        line = raw.strip()
        markers = _PAGE.findall(line)
        if markers:
            if lines or not _PAGE.match(line):
                next_page = int(markers[-1])
            else:
                page = int(markers[-1])
            line = _PAGE.sub("", line).strip()
            if not line:
                continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            text = _clean(heading.group(2))
            if text:
                items.append({"kind": "heading", "level": len(heading.group(1)), "text": text, "page": page})
        elif not line:
            flush()
        else:
            lines.append(line)
    flush()

    # 2. Paragraphs into sentences.
    segments: list[dict] = []
    para = 0
    for item in items:
        if item["kind"] == "heading":
            segments.append(item)
            continue
        for sentence in _segmenter.segment(item["text"]):
            sentence = sentence.strip()
            if re.search(r"\w", sentence):
                segments.append({"kind": "sentence", "para": para, "text": sentence, "page": item["page"]})
        para += 1
    return segments
