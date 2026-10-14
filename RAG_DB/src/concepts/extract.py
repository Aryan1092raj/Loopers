"""Concept extraction (TRD §7.2): one LLM call per window of about 1,500 characters, cached by content hash."""
import hashlib
import json
import re

from config import METADATA

WINDOW_CHARS = 1500
SAVE_EVERY = 10  # new LLM results between cache writes
CACHE = METADATA / "concept_cache.json"

PROMPT = (
    "List the concepts that this passage defines or teaches. Return only a JSON array of at most 5 objects "
    '{{"name": "<short canonical name, 1 to 4 words>", "definition": "<one sentence>", '
    '"level_hint": "concept" or "subtopic"}}. Return [] if the passage teaches no concept.\n\nPassage:\n{text}'
)


def windows(text: str, size: int = WINDOW_CHARS) -> list[str]:
    """Split text into windows of about `size` characters at word boundaries."""
    out, cur, n = [], [], 0
    for word in text.split():
        cur.append(word)
        n += len(word) + 1
        if n >= size:
            out.append(" ".join(cur))
            cur, n = [], 0
    if cur:
        out.append(" ".join(cur))
    return out


def parse_concepts(reply: str) -> list[dict]:
    found = re.search(r"\[.*\]", reply, re.S)
    try:
        items = json.loads(found.group(0)) if found else []
    except json.JSONDecodeError:
        return []
    return [
        {"name": str(i["name"]).strip(), "definition": str(i.get("definition", "")).strip(),
         "level_hint": "subtopic" if i.get("level_hint") == "subtopic" else "concept"}
        for i in items if isinstance(i, dict) and str(i.get("name", "")).strip()
    ]


def extract(units: list[dict], llm) -> list[dict]:
    """Concept mentions for units [{unit_id, text}], given in reading order.

    Returns [{unit_id, pos, name, definition, level_hint}] where `pos` counts windows in reading order.
    The cache is saved even when the LLM fails part way, so a rerun resumes.
    """
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    mentions, pos = [], 0
    try:
        for unit in units:
            for window in windows(unit["text"]):
                key = hashlib.sha1((PROMPT + window).encode()).hexdigest()
                if key not in cache:
                    cache[key] = parse_concepts(llm(PROMPT.format(text=window)))
                    if len(cache) % SAVE_EVERY == 0:  # the job runner SIGKILLs a stage on timeout, so `finally` below never runs
                        CACHE.parent.mkdir(parents=True, exist_ok=True)
                        CACHE.write_text(json.dumps(cache))
                mentions += [{**c, "unit_id": unit["unit_id"], "pos": pos} for c in cache[key]]
                pos += 1
    finally:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    return mentions
