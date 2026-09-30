"""Combined revision shorts — one "till endsem" PDF per course.

Generating notes is the expensive step and it already happened: every published
lecture carries a `Lecture.notesJson` that was paid for once. This phase
*condenses* that instead of generating anything new — a mechanical trim (free),
then one DeepSeek Flash call for the whole course, then LaTeX.

No retrieval, no Qdrant, no diagrams — and nothing on the retrieval path is
imported, not even for helpers. `notesJson` is the only input; lectures without
it are skipped by the caller and reported back.
"""

from __future__ import annotations

import argparse
import json
import re
import time

import cancel_flag
import config
import render_latex
from llm import call_deepseek

# NotesBlock union — src/pipeline/notes-prompt.ts. Anything not listed is dropped.
_KEEP_WHOLE = {"definition", "keyidea", "pitfall", "formula"}  # the Def/Key/Trap of a short
_TRUNCATE = {"paragraph", "intuition", "example", "note"}
_DROP = {"diagram", "code", "verse", "table"}  # tikz is the largest field in a notes doc

_SYSTEM = """You condense existing lecture notes into exam-revision shorts.

Rules:
- Use ONLY facts present in the input. Invent nothing, add no outside knowledge.
- One topic per major idea. Merge the same idea if it recurs across lectures.
- At most 120 words of prose per topic. Terse beats complete.
- No diagrams, no figures, no tables, no code. Text and math only.
- Keep the source language. If the notes are in Hindi, answer in Hindi.

Return JSON only, no prose around it, matching exactly:

{"topics": [{"title": "topic heading",
             "term": "the one thing to memorise, or \\"\\" if not definitional",
             "definition": "what it means, 1-3 sentences",
             "notation": "LaTeX math WITHOUT $ delimiters, or \\"\\"",
             "key": ["2-4 short bullets"],
             "why": "one line on why it matters, or \\"\\"",
             "trap": "the mistake students make, or \\"\\"",
             "source": "Lecture N - Title"}],
 "checklist": ["8-15 one-line exam checks covering the whole course"]}"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "revision"


# A JSON string escape is only \" \\ \/ \b \f \n \r \t \uXXXX. Anything else is a
# LaTeX command the model forgot to double (\frac, \neq, \sigma). \b\f\n\r\t are
# also doubled when a letter follows: mid-formula, "\frac" / "\to" is LaTeX far
# more often than a formfeed or a newline.
_BAD_ESC = re.compile(r'\\(?:u(?![0-9a-fA-F]{4})|[bfnrt](?=[A-Za-z])|[^"\\/bfnrtu])')


def _parse_json(text: str) -> dict:
    """Local on purpose. generate._parse_json is the hardened version, but that
    module owns retrieve() and this phase must not import the retrieval path.
    Its brace-balanced extraction exists to survive reasoning-output noise, and
    thinking_disabled=True means there is none."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Model returned an empty response — nothing to parse.")
    if t.startswith("```"):
        t = t.split("```", 2)[1].removeprefix("json").strip()
    # Repair first, not on failure: "\frac" is *legal* JSON (formfeed + "rac"),
    # so it parses clean and corrupts silently. Raw text is the fallback.
    # ponytail: cost is a real "\n" directly before a letter becoming literal.
    # Every field in the prompt is one line, so LaTeX wins that trade.
    try:
        return json.loads(_BAD_ESC.sub(lambda m: "\\" + m.group(0), t))
    except json.JSONDecodeError:
        return json.loads(t)


def _estimate_tokens(s: str) -> int:
    # ponytail: len//4 is the standard rough ratio; a real tokenizer would be a
    # dependency for a number only used to pick a split point.
    return len(s) // 4


def _trim_text(text: str, limit: int) -> str:
    """Truncate at a word boundary so the model never sees a cut-off word."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "..."


def _trim_block(b: dict) -> dict | None:
    t = b.get("type")
    if not t or t in _DROP:
        return None
    if t in _KEEP_WHOLE:
        return b
    if t == "bullets":
        items = [str(i).strip() for i in (b.get("items") or []) if str(i).strip()]
        return {"type": t, "items": items[: config.SHORTS_MAX_BULLETS]} if items else None
    if t in _TRUNCATE:
        text = _trim_text(b.get("text", ""), config.SHORTS_PROSE_CHARS)
        if not text:
            return None
        out = {"type": t, "text": text}
        if b.get("title"):
            out["title"] = b["title"]
        return out
    return None  # unknown block type — dropping is cheaper than guessing its shape


def _trim_notes(notes: dict) -> dict:
    """Strip a notesJson down to what a text-only revision short can use.

    Pure and free. `title`/`summary`/`keyTakeaways` survive verbatim — they are
    already condensed, so they carry the most value per token. `objectives` is
    dropped because it restates the section headings.
    """
    out = {
        "title": notes.get("title", ""),
        "summary": notes.get("summary", ""),
        "keyTakeaways": notes.get("keyTakeaways") or [],
        "sections": [],
    }
    for sec in notes.get("sections") or []:
        blocks = [tb for tb in (_trim_block(b) for b in (sec.get("blocks") or [])) if tb]
        if blocks or sec.get("heading"):
            out["sections"].append({"heading": sec.get("heading", ""), "blocks": blocks})
    return out


def _lecture_text(lec: dict) -> str:
    # ensure_ascii=False keeps Devanagari as-is; escaping it to \uXXXX would
    # roughly sextuple the token count of a Hindi course.
    return json.dumps(
        {
            "lecture": lec.get("title") or "Untitled",
            "date": str(lec.get("date") or "")[:10],
            "notes": _trim_notes(lec.get("notes") or {}),
        },
        ensure_ascii=False,
    )


def _split_groups(texts: list[str], budget: int) -> list[list[str]]:
    """Split on lecture boundaries only. A single oversized lecture gets its own
    group; the HTTP-400 retry below is what handles that case."""
    groups: list[list[str]] = []
    cur: list[str] = []
    cur_tok = 0
    for t in texts:
        tok = _estimate_tokens(t)
        if cur and cur_tok + tok > budget:
            groups.append(cur)
            cur, cur_tok = [], 0
        cur.append(t)
        cur_tok += tok
    if cur:
        groups.append(cur)
    return groups


def _condense(group: list[str], course: dict, cutoff: str) -> dict:
    user = (
        f"Course: {course.get('title', '')} ({course.get('code', '')})\n"
        f"Revision cutoff: {cutoff}\n"
        f"{len(group)} lectures, chronological:\n\n" + "\n\n".join(group)
    )
    # thinking_disabled: reasoning tokens bill as output, and this is extraction.
    raw = call_deepseek(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        max_tokens=16384,
        model=config.SHORTS_MODEL,
        thinking_disabled=True,
        role="shorts",
    )
    return _parse_json(raw)


def _condense_split_on_overflow(
    group: list[str], course: dict, cutoff: str, depth: int = 0
) -> list[dict]:
    """One call per group, halving on a context-overflow 400. call_deepseek raises
    a plain Exception carrying the response body, so the signal is sniffable."""
    try:
        return [_condense(group, course, cutoff)]
    except Exception as e:
        msg = str(e).lower()
        overflow = any(k in msg for k in ("context", "length", "400"))
        if not overflow or depth >= 2 or len(group) < 2:
            raise
        mid = len(group) // 2
        print(f"[shorts] {len(group)} lectures overflowed — halving and retrying")
        return _condense_split_on_overflow(
            group[:mid], course, cutoff, depth + 1
        ) + _condense_split_on_overflow(group[mid:], course, cutoff, depth + 1)


def _math(notation: str | None) -> str:
    """Wrap math in $...$ so s_definition takes the strip-and-\\[...\\] branch.
    Left bare, a backslash-free formula like `E = mc^2` falls to the escaped-text
    fallback and renders \\textasciicircum{} garbage."""
    n = (notation or "").strip().strip("$").strip()
    if n.startswith("\\[") and n.endswith("\\]"):
        n = n[2:-2].strip()
    return f"${n}$" if n else ""


def _build_spec(course: dict, cutoff: str, topics: list[dict], checklist: list[str]) -> dict:
    """Deterministic spec build using only handlers that already exist in
    render_latex.HANDLERS — this phase needs no renderer change."""
    slides: list[dict] = []
    for i, t in enumerate(topics, 1):
        num = str(i)
        title = (t.get("title") or f"Topic {i}").strip()
        src = (t.get("source") or "").strip()
        source = {"doc": src, "loc": ""} if src else None

        slides.append({"type": "section", "title": f"{num}. {title}", "number": num})

        definition = (t.get("definition") or "").strip()
        notation = _math(t.get("notation"))
        if definition or notation:
            d = {
                "type": "definition",
                "title": "Definition",
                "term": (t.get("term") or "").strip() or title,
                "body": definition,
            }
            if notation:
                d["notation"] = notation
            if source:
                d["source"] = source
            slides.append(d)

        lines = [f"Key: {k}" for k in (str(x).strip() for x in t.get("key") or []) if k]
        why = (t.get("why") or "").strip()
        if why:
            lines.append(f"Why: {why}")
        trap = (t.get("trap") or "").strip()
        if trap:
            lines.append(f"Trap: {trap}")
        if lines:
            b = {"type": "bullets", "title": "Key / Why / Trap", "bullets": lines}
            if source:
                b["source"] = source
            slides.append(b)

    if checklist:
        slides.append({"type": "summary", "title": "Exam Checklist", "takeaways": checklist})

    code = (course.get("code") or "").strip()
    return {
        "title": f"Revision - {course.get('title') or 'Course'}",
        "subtitle": f"{code + ' - ' if code else ''}uploaded on or before {cutoff}",
        "slides": slides,
    }


def generate(course: dict, cutoff: str, lectures: list[dict]) -> dict:
    """Condense every supplied lecture into one revision PDF. `lectures` is
    already filtered and ordered chronologically by the caller — chronological
    order is what earns prompt-prefix cache hits on re-runs."""
    if not lectures:
        raise ValueError("No lectures with notes to condense.")

    texts = [_lecture_text(lec) for lec in lectures]
    raw_tok = sum(
        _estimate_tokens(json.dumps(lec.get("notes") or {}, ensure_ascii=False))
        for lec in lectures
    )
    trim_tok = sum(_estimate_tokens(t) for t in texts)
    print(
        f"[shorts] {len(lectures)} lectures: {raw_tok} tok raw -> {trim_tok} tok trimmed "
        f"({trim_tok / max(raw_tok, 1):.0%})"
    )

    groups = _split_groups(texts, config.SHORTS_INPUT_BUDGET_TOK)
    print(f"[shorts] {len(groups)} call(s), model={config.SHORTS_MODEL}")

    topics: list[dict] = []
    checklist: list[str] = []
    seen: set[str] = set()
    for group in groups:
        cancel_flag.check()
        for result in _condense_split_on_overflow(group, course, cutoff):
            topics.extend(result.get("topics") or [])
            for c in result.get("checklist") or []:
                c = str(c).strip()
                if c and c.lower() not in seen:
                    seen.add(c.lower())
                    checklist.append(c)

    if not topics:
        raise ValueError("Model returned no topics — nothing to render.")

    spec = _build_spec(course, cutoff, topics, checklist)
    config.OUTPUT.mkdir(parents=True, exist_ok=True)
    out = config.OUTPUT / (
        f"REVISION-{_slug(course.get('title') or 'course')}-{time.strftime('%Y%m%d-%H%M%S')}.pdf"
    )
    render_latex.render(spec, out)
    print(f"[shorts] wrote {out.name}: {len(topics)} topics, {len(checklist)} checklist items")
    return {
        "pdf": out.name,
        "topics": len(topics),
        "calls": len(groups),
        "tokensEstimatedIn": trim_tok,
    }


_FIXTURE_BLOCKS = [
    {"type": "diagram", "tikz": "\\draw (0,0) -- (9,9);" * 60, "caption": "big"},
    {"type": "definition", "term": "Prior", "definition": "Belief before evidence."},
    {"type": "paragraph", "text": "word " * 400},
    {"type": "bullets", "items": [f"item {i}" for i in range(20)]},
    {"type": "code", "code": "print(1)", "language": "python"},
    {"type": "table", "headers": ["a"], "rows": [["b"]]},
    {"type": "formula", "latex": "P(A|B) = \\frac{P(B|A)P(A)}{P(B)}"},
]


def _selfcheck() -> None:
    """Smallest thing that fails if the trim breaks. No network, no LaTeX."""
    notes = {
        "title": "Bayes",
        "summary": "Sum",
        "objectives": ["drop me"],
        "keyTakeaways": ["keep me"],
        "sections": [{"heading": "H", "blocks": _FIXTURE_BLOCKS}],
    }
    t = _trim_notes(notes)
    kinds = [b["type"] for b in t["sections"][0]["blocks"]]
    assert "diagram" not in kinds and "code" not in kinds and "table" not in kinds, kinds
    assert "objectives" not in t, "objectives should be dropped"
    assert t["keyTakeaways"] == ["keep me"]
    para = next(b for b in t["sections"][0]["blocks"] if b["type"] == "paragraph")
    assert len(para["text"]) <= config.SHORTS_PROSE_CHARS + 3, len(para["text"])
    bul = next(b for b in t["sections"][0]["blocks"] if b["type"] == "bullets")
    assert len(bul["items"]) == config.SHORTS_MAX_BULLETS, len(bul["items"])

    raw = _estimate_tokens(json.dumps(notes, ensure_ascii=False))
    trimmed = _estimate_tokens(json.dumps(t, ensure_ascii=False))
    assert trimmed < raw * 0.4, f"trim kept {trimmed / raw:.0%}, want <40%"

    assert _math("E = mc^2") == "$E = mc^2$"
    assert _math("$x$") == "$x$"
    assert _math("\\[y\\]") == "$y$"
    assert _math("") == ""

    # Unescaped LaTeX in JSON is the failure mode this phase actually hits:
    # `\frac` is legal JSON (formfeed + "rac") and corrupts silently, `\sigma`
    # is illegal and raises. Both must come back as LaTeX.
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('{"n": "\\frac{1}{2}"}')["n"] == "\\frac{1}{2}"
    assert _parse_json('{"n": "\\sigma^2 \\neq \\rho"}')["n"] == "\\sigma^2 \\neq \\rho"
    assert _parse_json('{"n": "P(A|B)"}')["n"] == "P(A|B)"
    assert _parse_json('{"a": "x\\n y"}')["a"] == "x\n y", "\\n before a space survives"

    lec = "a" * 400  # 100 estimated tokens
    assert _split_groups([lec] * 4, 50) == [[lec]] * 4, "oversized lecture gets its own group"
    assert _split_groups([lec] * 4, 250) == [[lec] * 2] * 2, "should pack 2 per group"
    assert len(_split_groups([lec] * 4, 100_000)) == 1, "under budget = one call"

    # Spec build: proves every emitted slide type has a handler and the field names
    # match what render_latex actually reads.
    spec = _build_spec(
        {"title": "Prob & Stats", "code": "MA204"},
        "2026-04-30",
        [
            {
                "title": "Bayes Rule",
                "term": "Posterior",
                "definition": "Belief after evidence.",
                "notation": "P(A|B)",
                "key": ["normalise"],
                "why": "updates beliefs",
                "trap": "swapping the conditionals",
                "source": "Lecture 3 - Bayes",
            },
            {"title": "Nothing but a heading"},
        ],
        ["check one"],
    )
    kinds = [s["type"] for s in spec["slides"]]
    assert kinds == ["section", "definition", "bullets", "section", "summary"], kinds
    for s in spec["slides"]:
        assert s["type"] in render_latex.HANDLERS, f"no handler for {s['type']}"
    d = spec["slides"][1]
    assert d["body"] == "Belief after evidence.", "s_definition reads `body`, not `definition`"
    assert d["notation"] == "$P(A|B)$", d["notation"]
    assert d["source"] == {"doc": "Lecture 3 - Bayes", "loc": ""}
    assert spec["slides"][2]["bullets"] == [
        "Key: normalise",
        "Why: updates beliefs",
        "Trap: swapping the conditionals",
    ]
    assert spec["slides"][-1]["takeaways"] == ["check one"]
    print(f"[shorts] selfcheck ok — trim kept {trimmed / raw:.0%} of raw tokens")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate a combined revision PDF.")
    ap.add_argument("--input", help="JSON file: {course, cutoff, lectures}")
    ap.add_argument("--selfcheck", action="store_true", help="Run trim asserts and exit.")
    args = ap.parse_args()

    if args.selfcheck or not args.input:
        _selfcheck()
    else:
        payload = json.loads(open(args.input, encoding="utf-8").read())
        print(generate(payload["course"], payload["cutoff"], payload["lectures"]))
