"""Split a listening script (Markdown) into headings and sentences."""

import re
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import pysbd

_segmenter = pysbd.Segmenter(language="en", clean=False)
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_PAGE = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)


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
    segments: list[dict] = []
    para = 0
    page = None
    next_page = None  # a marker inside a paragraph applies once it ends
    lines: list[str] = []

    def flush() -> None:
        nonlocal para, page, next_page
        text = _clean(" ".join(lines))
        lines.clear()
        if text:
            for sentence in _segmenter.segment(text):
                sentence = sentence.strip()
                if re.search(r"\w", sentence):
                    segments.append({"kind": "sentence", "para": para, "text": sentence, "page": page})
            para += 1
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
                segments.append({"kind": "heading", "level": len(heading.group(1)), "text": text, "page": page})
        elif not line:
            flush()
        else:
            lines.append(line)
    flush()
    return segments
