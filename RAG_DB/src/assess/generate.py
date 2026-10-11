import json
import re
import uuid

from core.db import get_connection
from tutor.answer import complete

BLOOM_B = {"remember": -1.0, "understand": -0.5, "apply": 0.0, "analyze": 0.5, "evaluate": 1.0}
UNIT_CHARS = 1200
MAX_UNITS = 3

SYSTEM = (
    "You write one exam question from course material. Use ONLY the numbered excerpts; never rely on outside facts. "
    "Reply with a single JSON object and nothing else, with keys: "
    '"stem" (the question), "bloom" (one of remember, understand, apply, analyze), '
    '"answer" (see below), "explanation" (one or two sentences saying why the answer is right, using the excerpts), '
    '"distractor_notes" (object mapping each wrong option to the misconception that makes it tempting; {} if not mcq), '
    '"options" (list of exactly 4 option strings if type is mcq, else []), "tolerance" (relative tolerance, numeric only, else null). '
    'For mcq, "answer" is the exact text of the one correct option. For short, "answer" is a one-sentence model answer. '
    'For numeric, "answer" is the number as a string with no units.'
)


class NoMaterial(ValueError):
    """The concept has no tagged source units to write a question from."""


def concept_units(concept_id: str) -> tuple[str, list[tuple[str, str]]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT name FROM concepts WHERE concept_id=?", (concept_id,)).fetchone()
        units = conn.execute(
            # A topic has no units of its own; it draws on the units of its child concepts.
            "SELECT u.unit_id, MAX(u.text) FROM unit_concepts uc JOIN units u ON u.unit_id=uc.unit_id"
            " WHERE uc.concept_id=? OR uc.concept_id IN (SELECT concept_id FROM concepts WHERE parent_id=?)"
            " GROUP BY u.unit_id ORDER BY MAX(uc.score) DESC LIMIT ?", (concept_id, concept_id, MAX_UNITS)
        ).fetchall()
    finally:
        conn.close()
    if not row or not units:
        raise NoMaterial(f"no source material tagged to concept {concept_id}")
    return row[0], units


def _parse(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        raise ValueError("generator reply has no JSON object")
    return json.loads(m.group(0))


def generate_item(concept_id: str, topic_id: str, item_type: str = "mcq", custom_stem: str | None = None) -> dict:
    """Write one item grounded in the concept's top source units; unit_ids_json records exactly what it was written from."""
    name, units = concept_units(concept_id)
    excerpts = "\n\n".join(f"[{i}] {text[:UNIT_CHARS]}" for i, (_, text) in enumerate(units, 1))
    ask = f"Concept: {name}\nType: {item_type}\n" + (f"Question must be about: {custom_stem}\n" if custom_stem else "") + f"\nExcerpts:\n{excerpts}"
    d = _parse(complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": ask}], max_tokens=900))

    options = [str(o) for o in d.get("options") or []]
    answer = str(d["answer"]).strip()
    if item_type == "mcq" and (len(options) != 4 or answer not in options):
        raise ValueError("generator mcq must have 4 options including the answer")
    bloom = d.get("bloom") if d.get("bloom") in BLOOM_B else "apply"
    return {
        "item_id": f"item_{uuid.uuid4().hex[:8]}",
        "concept_id": concept_id,
        "topic_id": topic_id,
        "type": item_type,
        "bloom": bloom,
        "stem": str(d["stem"]).strip(),
        "options_json": json.dumps(options) if item_type == "mcq" else None,
        "answer_json": json.dumps([answer]),
        "rubric_json": json.dumps({"explanation": d.get("explanation", ""), "distractor_notes": d.get("distractor_notes") or {},
                                   "tolerance": d.get("tolerance")}),
        "unit_ids_json": json.dumps([u for u, _ in units]),
        "difficulty_b": BLOOM_B[bloom],  # ponytail: Bloom-level prior, recalibrate from attempts when there are enough
        "discrimination_a": 1.0,
    }
