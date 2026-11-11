"""Join Gemini's parts into one script, cleanly across page and part breaks.

Gemini writes the script in parts of about 10 pages, and each part repeats
the previous part's last page so a sentence cut off at the end can be
finished. Three things can go wrong where parts and pages meet, and this
fixes them without trusting Gemini to get it right:

- A part starts that shared page again from the top, so text is read twice.
  `drop_repeated_start` removes whatever the start of a part repeats from
  the end of the script so far (and nothing anywhere else, since documents do
  legitimately repeat themselves).
- A sentence is split into two paragraphs at a page break ("…those who are
  looking for" / "stable, long-term exposure…"). `join_split_sentences`
  joins a paragraph that doesn't end a sentence to a next one that starts in
  lower case, moving any page marker between them to after the paragraph.
- A word hyphenated across a page break ("pat-" / "tern", or "low-" /
  "beta"). `join_words` keeps the hyphen when the document writes the word
  with one elsewhere, and drops it otherwise.
"""

import re

from .segments import PAGE_MARKER

REPEAT_WINDOW = 1500   # words at the end of the script that a new part may repeat (over a page)
REPEAT_SHARE = 0.5     # a paragraph or sentence this much made of those words is a repeat
_GRAM = 6              # compare runs of this many words
_SENTENCE_END = re.compile(r"""[.?!:;…"”’)\]]$""")
_SENTENCES = re.compile(r"(?<=[.?!…])[\"”’)]*\s+(?=[A-Z0-9\"“‘(])")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", PAGE_MARKER.sub(" ", text).lower())


def _grams(words: list[str]) -> set[tuple]:
    return {tuple(words[i:i + _GRAM]) for i in range(len(words) - _GRAM + 1)}


def _is_repeat(text: str, previous_grams: set, previous_units: set) -> bool:
    words = _words(text)
    if not words:
        return True
    if len(words) < _GRAM:
        # A heading or short sentence repeats only if that exact heading or
        # sentence came just before (the words alone could be anywhere).
        return " ".join(words) in previous_units
    grams = [tuple(words[i:i + _GRAM]) for i in range(len(words) - _GRAM + 1)]
    return sum(g in previous_grams for g in grams) / len(grams) >= REPEAT_SHARE


def _units(text: str) -> set[str]:
    """Every block (paragraph or heading) and every sentence of `text`, normalised."""
    units = set()
    for block in re.split(r"\n\s*\n", text):
        _, body = _split_markers(block.strip())
        body = body.lstrip("#").strip()
        if body:
            units.add(" ".join(_words(body)))
            units.update(" ".join(_words(s)) for s in _SENTENCES.split(body))
    return units


def _split_markers(block: str) -> tuple[list[str], str]:
    """A block's leading page-marker lines, and the rest of it."""
    lines = block.split("\n")
    markers = []
    while lines and PAGE_MARKER.fullmatch(lines[0].strip()):
        markers.append(lines.pop(0).strip())
    return markers, "\n".join(lines).strip()


def drop_repeated_start(previous: str, new: str) -> tuple[str, int]:
    """Remove the start of `new` that repeats the end of `previous`.

    Works paragraph by paragraph, then sentence by sentence inside the first
    paragraph that isn't wholly a repeat, and stops at the first new text.
    Returns (`new` without the repeat, number of words removed). The latest
    page marker passed over is kept, so the page numbers stay right.
    """
    previous_words = _words(previous)[-REPEAT_WINDOW:]
    if not previous_words:
        return new, 0
    previous_grams = _grams(previous_words)
    # Blocks and sentences from roughly the same stretch (judged by characters).
    previous_units = _units(previous[-REPEAT_WINDOW * 8:])

    blocks = [b.strip() for b in re.split(r"\n\s*\n", new.strip()) if b.strip()]
    removed = 0
    last_marker = None
    for i, block in enumerate(blocks):
        markers, body = _split_markers(block)
        if markers:
            last_marker = markers[-1]
        if not body:
            continue
        # Drop the repeated sentences at the start of this block; keep everything
        # from the first new one. A block that's all repeats goes, and we move on.
        sentences = [s for s in _SENTENCES.split(body) if s.strip()]
        keep_from = 0
        while keep_from < len(sentences) and _is_repeat(sentences[keep_from], previous_grams, previous_units):
            removed += len(_words(sentences[keep_from]))
            keep_from += 1
        if keep_from == len(sentences):
            continue
        first = " ".join(sentences[keep_from:]) if keep_from else body
        head = [last_marker] if last_marker else []
        return "\n\n".join(head + [first] + blocks[i + 1:]), removed
    return "", removed  # the whole part was a repeat


def find_repeats(script: str, min_words: int = 40, within: int = REPEAT_WINDOW) -> list[int]:
    """Pages where a stretch of at least `min_words` repeats text from shortly
    before it. A safety check after stitching; documents that restate a
    passage much later (an appendix, say) aren't flagged."""
    words, pages, page = [], [], None
    for piece in re.split(r"(<!--\s*page\s+\d+\s*-->)", script, flags=re.I):
        marker = PAGE_MARKER.fullmatch(piece.strip())
        if marker:
            page = int(marker.group(1))
            continue
        for word in re.findall(r"[a-z0-9]+", piece.lower()):
            words.append(word)
            pages.append(page)
    seen: dict[tuple, int] = {}
    flagged, run = set(), 0
    for i in range(len(words) - 7):
        gram = tuple(words[i:i + 8])
        earlier = seen.get(gram)
        if earlier is not None and 8 < i - earlier <= within:
            run += 1
            if run + 7 >= min_words and pages[i] is not None:
                flagged.add(pages[i])
        else:
            run = 0
        seen.setdefault(gram, i)
    return sorted(flagged)


def join_words(left: str, right: str, document_text: str | None) -> str:
    """Join text on either side of a break, where the break may split a word.

    `left` ending in a hyphen ("low-" + "beta"), or `document_text` showing
    the word was hyphenated at a line or page end, means a split word: keep
    the hyphen if the document writes it hyphenated elsewhere ("low-beta"),
    otherwise close it up ("pat-" + "tern" = "pattern")."""
    left, right = left.rstrip(), right.lstrip()
    tail = re.search(r"([A-Za-z]+)(-?)$", left)
    head = re.match(r"([a-z]+)", right)
    if not tail or not head:
        return f"{left} {right}"
    first, second = tail.group(1), head.group(1)
    split_word = bool(tail.group(2))
    if not split_word and document_text:
        split_word = re.search(rf"\b{re.escape(first)}-\s*\n\s*{re.escape(second)}", document_text) is not None
    if not split_word:
        return f"{left} {right}"
    stem = left[:len(left) - len(tail.group(2))] if tail.group(2) else left
    if not document_text:
        return f"{stem}-{right}" if tail.group(2) else f"{left} {right}"
    hyphenated = len(re.findall(rf"\b{re.escape(first)}-{re.escape(second)}\b", document_text, re.I))
    closed = len(re.findall(rf"\b{re.escape(first + second)}\b", document_text, re.I))
    if hyphenated != closed:
        return f"{stem}-{right}" if hyphenated > closed else f"{stem}{right}"
    # Neither spelling appears elsewhere: two real words ("high" + "beta") make a
    # compound; fragments ("pat" + "tern") were a word broken at the line end.
    def standalone(word):
        return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", document_text, re.I) is not None

    if len(first) > 1 and len(second) > 1 and standalone(first) and standalone(second):
        return f"{stem}-{right}"
    return f"{stem}{right}"


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.rstrip()))


def _continues(text: str) -> bool:
    return bool(re.match(r"[a-z]", text.lstrip()))


def join_split_sentences(script: str, document_text: str | None = None) -> str:
    """Join a paragraph that stops mid-sentence to the next one, which starts
    in lower case, across any page markers between them (moved to after)."""
    out: list[str] = []          # finished blocks
    para_at: int | None = None   # index in `out` of the last paragraph, if nothing but markers follow it
    for block in (b.strip() for b in re.split(r"\n\s*\n", script.strip())):
        if not block:
            continue
        markers, body = _split_markers(block)
        if not body:                                  # only page markers
            out.extend(markers)
            continue
        if body.startswith("#"):                      # a heading ends any sentence
            out.extend(markers + [body])
            para_at = None
            continue
        if para_at is not None and not _ends_sentence(out[para_at]) and _continues(body):
            out[para_at] = join_words(out[para_at], body, document_text)
            out.extend(markers)                       # markers now come after the joined paragraph
            continue
        out.extend(markers + [body])
        para_at = len(out) - 1
    # Markers that sat between the halves are now after the paragraph; tidy any
    # that ended up before a later marker with nothing in between.
    tidy: list[str] = []
    for block in out:
        if PAGE_MARKER.fullmatch(block) and tidy and PAGE_MARKER.fullmatch(tidy[-1]):
            tidy[-1] = block
        else:
            tidy.append(block)
    return "\n\n".join(tidy)
