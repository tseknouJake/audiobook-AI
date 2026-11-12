"""Ask questions about a reading, from where you are in it.

Nothing is sent until you ask. Each question is one Gemini request carrying:
- the whole reading as numbered paragraphs with page numbers, built from the
  same sentences the player highlights;
- your place: the sentence being read, marked, or the text you selected;
- the last few questions and answers, so follow-ups like "simpler still" work.

The chatbot has its own models (GEMINI_CHAT_MODELS), separate from the ones
that write scripts, so questions don't use up the free allowance that script
writing relies on, and a daily cap (AUDIOBOOK_CHAT_DAILY_LIMIT), counted in
library/chat_usage.json. Chats are saved per reading in chat.json.
"""

import json
import os
import re
import threading
from datetime import date, datetime

from . import config, library

SYSTEM_PROMPT = """\
You help a university student understand a reading they are listening to and \
following on screen. You get the whole reading as numbered paragraphs, written \
[¶N p.P] for paragraph N on page P, with the student's current place marked \
⟦like this⟧.

- "This", "here" and "it" mean the marked text and its paragraph, unless the \
student says otherwise. "The previous paragraph", "the section before" and \
"earlier" are relative to the marked place.
- Answer from the reading. If the reading doesn't cover something, say so \
plainly; you may then add brief general background, clearly labelled as not \
from the reading.
- Keep it short: two to five sentences unless the student asks for more. Use \
plain language and explain any jargon.
- Say where your answer comes from with page numbers, like (p. 6).
- Plain text only: no tables, headings, bold or bullet points.
"""

# One-click questions shown as buttons: key -> (button label, question sent)
QUICK = {
    "simplify": ("Simplify this", "Simplify this."),
    "explain": ("Explain this better", "Explain this better, with an example."),
    "define": ("Define this term", "Define the key term here, as this reading uses it."),
    "equation": ("Explain the equation", "Explain the equation here in plain words."),
    "why": ("Why does this matter?", "Why does this matter for the reading's overall argument?"),
    "previous": ("Previous paragraph", "What did the previous paragraph mean?"),
    "section": ("Summarise this section", "Summarise this section."),
}

HISTORY_TURNS = 6          # earlier questions and answers sent with each question
REQUEST_TIMEOUT_MS = 45_000  # per model: then move on to the next (Flash-Lite answers in about a second)
# Quick buttons that don't need deep thought: answered with less thinking, so faster.
LIGHT = {"simplify", "explain", "define", "equation", "previous"}
_lock = threading.Lock()


class ChatError(RuntimeError):
    """A problem to show in the Ask tab, in plain words."""


class LimitReached(ChatError):
    pass


# ---------- daily cap ----------

def _usage_file():
    return config.LIBRARY_DIR / "chat_usage.json"


def usage() -> dict:
    """Questions answered today and how many are left (the cap resets at midnight)."""
    today = date.today().isoformat()
    used = 0
    path = _usage_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            used = data.get("used", 0) if data.get("date") == today else 0
        except (json.JSONDecodeError, OSError):
            used = 0
    limit = config.CHAT_DAILY_LIMIT
    return {"date": today, "used": used, "limit": limit, "left": max(0, limit - used)}


def _count_question() -> None:
    with _lock:
        current = usage()
        path = _usage_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"date": current["date"], "used": current["used"] + 1}), encoding="utf-8")
        os.replace(tmp, path)


# ---------- saved chats ----------

def _chat_file(slug: str):
    return library.reading_dir(slug) / "chat.json"


def history(slug: str) -> list[dict]:
    path = _chat_file(slug)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(slug: str, messages: list[dict]) -> None:
    path = _chat_file(slug)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(messages, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def clear(slug: str) -> None:
    _chat_file(slug).unlink(missing_ok=True)


# ---------- the request ----------

def _paragraphs(timing: dict) -> list[dict]:
    """Group the player's segments into paragraphs: [{"n", "page", "heading", "parts": [(id, text)]}]."""
    paras, current = [], None
    for seg in timing.get("segments", []):
        if seg["kind"] == "heading":
            current = None
            paras.append({"page": seg.get("page"), "heading": "#" * seg.get("level", 2),
                          "parts": [(seg["id"], seg["text"])]})
            continue
        if current is None or current["para"] != seg["para"]:
            current = {"page": seg.get("page"), "heading": None, "para": seg["para"], "parts": []}
            paras.append(current)
        current["parts"].append((seg["id"], seg["text"]))
    for n, para in enumerate(paras, 1):
        para["n"] = n
    return paras


def build_prompt(timing: dict, question: str, segment_id: int | None = None,
                 selection: str | None = None, earlier: list[dict] | None = None) -> tuple[str, dict]:
    """The text sent to Gemini, and a short description of what "this" was."""
    paras = _paragraphs(timing)
    selection = re.sub(r"\s+", " ", selection or "").strip()[:2000]
    # With a selection, `segment_id` is the sentence it starts in: mark that copy of
    # the words, not the first place they appear in the reading.
    selected_in = None
    if selection and segment_id is not None:
        selected_in = next((s for s in timing.get("segments", []) if s["id"] == segment_id
                            and selection[:60] in s["text"]), None)
    lines, about = [], {}
    for para in paras:
        texts = []
        for seg_id, text in para["parts"]:
            if (selection and not about and selection in text
                    and (selected_in is None or seg_id == selected_in["id"])):
                text = text.replace(selection, f"⟦{selection}⟧", 1)
                about = {"n": para["n"], "page": para["page"], "text": selection, "kind": "selection"}
            elif not selection and seg_id == segment_id and not about:
                about = {"n": para["n"], "page": para["page"], "text": text, "kind": "sentence"}
                text = f"⟦{text}⟧"
            texts.append(text)
        body = " ".join(texts)
        prefix = f"{para['heading']} " if para["heading"] else ""
        lines.append(f"[¶{para['n']} p.{para['page']}] {prefix}{body}")

    if selection and not about:
        # The selection spans sentences: find the paragraph that contains its start.
        start = selection[:60]
        for i, para in enumerate(paras):
            if start in " ".join(t for _, t in para["parts"]):
                lines[i] = lines[i].replace(start, "⟦" + start, 1) + "⟧"
                about = {"n": para["n"], "page": para["page"], "text": selection, "kind": "selection"}
                break

    if about:
        section = next((" ".join(t for _, t in p["parts"]) for p in reversed(paras[:about["n"]]) if p["heading"]), "")
        what = "the text they selected" if about["kind"] == "selection" else "the sentence being read"
        where = (f"The student is at ¶{about['n']} (page {about['page']})"
                 + (f", in the section \"{section}\"" if section else "") + f"; {what} is marked ⟦…⟧.")
        about["section"] = section
    else:
        where = "The student's place in the reading isn't known; answer about the reading as a whole."

    chat = ""
    if earlier:
        turns = [f"Student: {m['question']}\nYou: {m['answer']}" for m in earlier[-HISTORY_TURNS:] if m.get("answer")]
        if turns:
            chat = "Earlier in this conversation:\n\n" + "\n\n".join(turns) + "\n\n"
    prompt = "<reading>\n" + "\n".join(lines) + "\n</reading>\n\n" + where + "\n\n" + chat + "Question: " + question
    return prompt, about


def _client():
    from google import genai
    from google.genai import types

    return genai.Client(http_options=types.HttpOptions(
        timeout=REQUEST_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))


def _generate(prompt: str, light: bool = False) -> tuple[str, str]:
    """(answer, model): try each chat model in turn; busy, slow or out-of-quota ones
    are skipped. `light` asks for less thinking, for quick questions."""
    from google.genai import errors, types

    client = _client()
    problems = []
    for model in config.GEMINI_CHAT_MODELS:
        settings = {"system_instruction": SYSTEM_PROMPT}
        if light:
            settings["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        try:
            try:
                response = client.models.generate_content(
                    model=model, contents=[prompt], config=types.GenerateContentConfig(**settings))
            except errors.ClientError as exc:
                if not (light and exc.code == 400 and "think" in (exc.message or "").lower()):
                    raise
                settings.pop("thinking_config")  # this model doesn't take a thinking level
                response = client.models.generate_content(
                    model=model, contents=[prompt], config=types.GenerateContentConfig(**settings))
        except errors.ClientError as exc:
            if exc.code == 429:
                problems.append(f"{model}: free limit reached")
                continue
            if exc.code == 404:
                problems.append(f"{model}: not available with this key")
                continue
            raise ChatError(f"Gemini didn't accept the question ({exc.message or exc.code}).") from None
        except Exception as exc:  # overloaded, or timed out
            if isinstance(exc, errors.ServerError) or "timeout" in type(exc).__name__.lower():
                problems.append(f"{model}: busy")
                continue
            raise
        text = (response.text or "").strip()
        if text:
            return text, model
        problems.append(f"{model}: empty answer")
    raise ChatError("Gemini is busy right now, so there's no answer. Try again in a minute. "
                    f"({'; '.join(problems)})")


def ask(slug: str, question: str = "", quick: str | None = None,
        segment_id: int | None = None, selection: str | None = None) -> dict:
    """Answer one question and save it to the reading's chat."""
    if not config.has_gemini_key():
        raise ChatError("Add a Gemini key first (run ./run.sh, or see the README).")
    if quick:
        if quick not in QUICK:
            raise ChatError("Unknown quick question.")
        question = QUICK[quick][1]
    question = (question or "").strip()[:2000]
    if not question:
        raise ChatError("Type a question first.")
    if usage()["left"] <= 0:
        raise LimitReached(f"That's today's {config.CHAT_DAILY_LIMIT} questions used up "
                           "(the limit protects your free Gemini allowance). It resets at midnight.")

    timing_path = library.reading_dir(slug) / "segments.json"
    if not timing_path.exists():
        raise ChatError("This reading isn't ready yet.")
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    messages = history(slug)
    prompt, about = build_prompt(timing, question, segment_id, selection, messages)

    answer, model = _generate(prompt, light=quick in LIGHT)
    _count_question()
    entry = {"question": question, "quick": quick, "answer": answer, "model": model,
             "about": {k: about[k] for k in ("page", "text", "kind", "section") if k in about},
             "time": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        messages = history(slug)
        messages.append(entry)
        _save(slug, messages)
    return {**entry, "usage": usage()}
