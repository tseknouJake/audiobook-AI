"""Split a listening script (Markdown) into headings and sentences."""

import re
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import pysbd

_segmenter = pysbd.Segmenter(language="en", clean=False)
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _clean(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text)  # stray Markdown emphasis
    return re.sub(r"\s+", " ", text).strip()


def parse_script(markdown: str) -> list[dict]:
    """Return segments in reading order.

    Each is {"kind": "heading", "level", "text"} or
    {"kind": "sentence", "para", "text"}, where `para` numbers the paragraphs.
    """
    segments: list[dict] = []
    para = 0
    lines: list[str] = []

    def flush() -> None:
        nonlocal para
        text = _clean(" ".join(lines))
        lines.clear()
        if not text:
            return
        for sentence in _segmenter.segment(text):
            sentence = sentence.strip()
            if re.search(r"\w", sentence):
                segments.append({"kind": "sentence", "para": para, "text": sentence})
        para += 1

    for raw in markdown.splitlines():
        line = raw.strip()
        heading = _HEADING.match(line)
        if heading:
            flush()
            text = _clean(heading.group(2))
            if text:
                segments.append({"kind": "heading", "level": len(heading.group(1)), "text": text})
        elif not line:
            flush()
        else:
            lines.append(line)
    flush()
    return segments
