"""The reading's PDF, for the side pane in the player.

Pages are rendered to JPEGs on first view. Tables, figures and equations are
found with PyMuPDF's layout model, which runs locally (no Gemini requests),
and matched to their captions so "Table 2" in the script can link to them.
Scanned pages have no text layer, so nothing is listed for them.
"""

import json
import re
import threading
from pathlib import Path

import pymupdf

RENDER_WIDTH = 1600  # pixels; sharp enough to zoom in on a table
ANALYSIS_VERSION = 1

_LABEL = re.compile(r"^\s*(Table|Figure|Fig\.?|Exhibit|Chart)\s+([A-Z]?\d+[a-z]?|[IVXLC]+)\b", re.I)
# The "(3)" beside a displayed equation; text extraction may put it first or last.
_EQ_NUMBER = re.compile(r"^\s*\(([A-Z]?\.?\d{1,3}[a-z]?)\)|\(([A-Z]?\.?\d{1,3}[a-z]?)\)\s*$")
_KINDS = {"table": "table", "figure": "figure", "fig": "figure", "fig.": "figure",
          "chart": "figure", "exhibit": "exhibit"}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(folder: Path) -> threading.Lock:
    with _guard:
        return _locks.setdefault(str(folder), threading.Lock())


def item_number(raw: str) -> str:
    """'IV' -> '4', '2' -> '2', 'A1' -> 'a1': the same key whichever way it's written."""
    if re.fullmatch(r"[IVXLC]+", raw, re.I):
        total, previous = 0, 0
        for ch in reversed(raw.upper()):
            value = _ROMAN[ch]
            total += -value if value < previous else value
            previous = max(previous, value)
        return str(total)
    return raw.lower()


def page_image(folder: Path, pdf_path: Path, number: int) -> Path:
    out = folder / "pages" / f"p{number:03d}.jpg"
    if out.exists():
        return out
    with _lock_for(folder), pymupdf.open(pdf_path) as doc:
        if out.exists():
            return out
        if not 1 <= number <= doc.page_count:
            raise IndexError(number)
        page = doc[number - 1]
        zoom = RENDER_WIDTH / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        out.parent.mkdir(exist_ok=True)
        tmp = out.with_name(out.stem + ".tmp.jpg")
        pix.save(str(tmp), jpg_quality=82)
        tmp.replace(out)
    return out


def info(folder: Path, pdf_path: Path) -> dict:
    """Page sizes plus the tables, figures and equations found (cached in pdf.json)."""
    cache = folder / "pdf.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("version") == ANALYSIS_VERSION:
            return data
    with _lock_for(folder):
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == ANALYSIS_VERSION:
                return data
        data = _analyse(pdf_path)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


def _box_rect(box: dict) -> pymupdf.Rect:
    return pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])


def _analyse(pdf_path: Path) -> dict:
    import pymupdf4llm

    with pymupdf.open(pdf_path) as doc:
        pages = [{"w": round(p.rect.width, 1), "h": round(p.rect.height, 1)} for p in doc]
        layout = json.loads(pymupdf4llm.to_json(doc, use_ocr=False, show_progress=False))
        items = []
        for page_data in layout["pages"]:
            number = int(page_data["page_number"])
            page = doc[number - 1]
            if not page.get_text("text").strip():
                continue  # scanned page: the layout model has no text to work with
            items += _page_items(page, number, page_data["boxes"])
    items.sort(key=lambda it: (it["page"], it["bbox"][1]))
    return {"version": ANALYSIS_VERSION, "pages": pages, "items": items}


def _page_items(page, number: int, boxes: list[dict]) -> list[dict]:
    width, height = page.rect.width, page.rect.height

    # (box, rect, raw text with line breaks)
    texts = [(box, _box_rect(box), page.get_text("text", clip=_box_rect(box))) for box in boxes]
    found = []
    for box, rect, raw in texts:
        cls = box.get("boxclass")
        if cls not in ("table", "picture", "formula"):
            continue
        if cls == "picture" and rect.get_area() < 0.02 * width * height:
            continue  # logos and icons
        inner = " ".join(raw.split())

        item = {"page": number, "key": None}
        if cls == "formula":
            match = _EQ_NUMBER.search(inner)
            item.update(kind="equation", caption=inner[:140])
            if match:
                num = match.group(1) or match.group(2)
                item.update(label=f"Equation {num}", key=f"equation-{item_number(num)}")
            else:
                item["label"] = "Equation"
        else:
            kind = "table" if cls == "table" else "figure"
            caption = _find_caption(rect, raw, texts, box, height)
            if caption:
                cap_rect, cap_text, match = caption
                kind = _KINDS.get(match.group(1).lower(), kind)
                item.update(label=f"{kind.title()} {match.group(2)}",
                            key=f"{kind}-{item_number(match.group(2))}", caption=cap_text[:180])
                if cap_rect is not None:
                    rect = rect | cap_rect  # show the caption with it
            else:
                item.update(label=kind.title(), caption="")
            item["kind"] = kind

        pad = 4
        item["bbox"] = [round(max(0, rect.x0 - pad) / width, 4), round(max(0, rect.y0 - pad) / height, 4),
                        round(min(width, rect.x1 + pad) / width, 4), round(min(height, rect.y1 + pad) / height, 4)]
        found.append(item)
    return found


def _find_caption(rect, raw: str, texts, own_box, page_height):
    """The caption ("Table 2: ...") inside the box, or the nearest one just above or below.
    Returns (caption rect or None if inside, caption text, label match)."""
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    match = _LABEL.match(first_line)
    if match:
        return None, first_line, match
    best = None
    for box, other, other_raw in texts:
        if box is own_box or box.get("boxclass") in ("page-header", "page-footer"):
            continue
        text = " ".join(other_raw.split())
        match = _LABEL.match(text)
        if not match:
            continue
        if other.y0 >= rect.y1:
            gap = other.y0 - rect.y1
        elif other.y1 <= rect.y0:
            gap = rect.y0 - other.y1
        else:
            gap = 0
        overlap = min(other.x1, rect.x1) - max(other.x0, rect.x0)
        if gap > 0.12 * page_height or overlap < 0.3 * min(other.width, rect.width):
            continue
        # A real caption ("Figure 1:" / "Figure 1." / a caption box) beats a sentence
        # that merely starts with "Figure 1 shows…", even if that sentence is closer.
        caption_like = box.get("boxclass") == "caption" or re.match(r"\s*[:.\-–—]", text[match.end():])
        score = gap + (0 if caption_like else 0.2 * page_height)
        if best is None or score < best[0]:
            best = (score, other, text, match)
    return best[1:] if best else None
