"""Revision flashcards (PS 6b): cards for a student's weakest concepts, cited to stored units.

The model writes [E1] markers only; `tutor.cite` turns them into page, slide or timestamp citations.
"""
import json
import math

from core.db import get_connection
from core.models import Evidence
from tutor.answer import NoProvider, complete
from tutor.cite import MARKER, body, citations_for
from tutor.retrieve import to_unit

UNITS_PER_CONCEPT = 3
EVIDENCE_CHARS = 1000  # per unit in the prompt
EXTRACT_CHARS = 300    # per unit when no LLM is configured

SYSTEM = (
    "You write flashcards for a student revising a weak topic. Use ONLY the numbered evidence. "
    'Reply with a JSON array of {{"front": question, "back": answer, "ev": ["E1"]}} and nothing else. '
    "Every card cites at least one evidence id in ev. Never write page numbers, slide numbers or timestamps. "
    "Reply in the language with code {lang}."
)


def weakest(student_id: str, n: int, concept_ids: list[str] | None = None) -> list[tuple[str, str]]:
    """(concept_id, name) of the n concepts with tagged units and the lowest mastery. No history counts as 0.1."""
    where, args = "", [student_id]
    if concept_ids:
        where = f"WHERE c.concept_id IN ({','.join('?' * len(concept_ids))})"
        args += concept_ids
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT c.concept_id, c.name FROM concepts c JOIN unit_concepts uc ON uc.concept_id = c.concept_id"
            " LEFT JOIN learner_state ls ON ls.concept_id = c.concept_id AND ls.student_id = ?"
            f" {where} GROUP BY c.concept_id ORDER BY COALESCE(MAX(ls.p_mastery), 0.1), c.name LIMIT ?",
            [*args, n],
        ).fetchall()
    finally:
        conn.close()


def evidence_for(concept_id: str, start: int = 1, limit: int = UNITS_PER_CONCEPT) -> list[Evidence]:
    """The best tagged text units of a concept as Evidence E{start}..; figures are skipped."""
    conn = get_connection()
    conn.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
    try:
        rows = conn.execute(
            "SELECT u.unit_id, u.source_id, u.kind, u.heading, u.text, u.page_no, u.slide_no, u.t_start, u.t_end,"
            " u.matched_slide_page, u.bbox_json AS bbox, s.title, s.kind AS source_type"
            " FROM unit_concepts uc JOIN units u ON u.unit_id = uc.unit_id LEFT JOIN sources s ON s.source_id = u.source_id"
            " WHERE uc.concept_id = ? AND u.kind != 'figure' ORDER BY uc.score DESC LIMIT ?",
            (concept_id, limit),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        r["bbox"] = json.loads(r["bbox"]) if r["bbox"] else None
    return [Evidence(id=f"E{start + i}", unit=to_unit(r), score=1.0) for i, r in enumerate(rows)]


def evidence_block(evidence: list[Evidence]) -> str:
    return "\n\n".join(
        f"[{e.id}] {e.unit.title}\n{body(e.unit.text, e.unit.heading)[:EVIDENCE_CHARS]}" for e in evidence
    )


def _cards_for(name: str, evidence: list[Evidence], n: int, lang: str) -> list[dict]:
    try:
        reply = complete(
            [{"role": "system", "content": SYSTEM.format(lang=lang)},
             {"role": "user", "content": f"Concept: {name}\nCards: {n}\n\nEvidence:\n{evidence_block(evidence)}"}],
            max_tokens=1500,
        )
        raw = json.loads(reply[reply.index("["):reply.rindex("]") + 1])
    except (NoProvider, ValueError):  # no LLM, or a reply that is not a JSON array: quote the best evidence
        first = evidence[0]
        raw = [{"front": f"What should you remember about {name}?",
                "back": body(first.unit.text, first.unit.heading)[:EXTRACT_CHARS].strip(), "ev": [first.id]}]
    ids = {e.id for e in evidence}
    cards = []
    for c in raw[:n] if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        used = [i for i in c.get("ev") or [] if i in ids]
        front, back = (MARKER.sub("", str(c.get(k) or "")).strip() for k in ("front", "back"))
        if front and back and used:
            cards.append({"front": front, "back": back, "citations": citations_for([e for e in evidence if e.id in used])})
    return cards


def flashcards(student_id: str, concept_ids: list[str] | None = None, n: int = 10, lang: str = "en") -> list[dict]:
    """Up to n cited cards spread over the student's weakest concepts (all of concept_ids when given)."""
    concepts = weakest(student_id, len(concept_ids) if concept_ids else 3, concept_ids)
    per = math.ceil(n / len(concepts)) if concepts else 0
    out = []
    for cid, name in concepts:
        evidence = evidence_for(cid)
        if evidence:
            out += [{"concept_id": cid, **c} for c in _cards_for(name, evidence, per, lang)]
    return out[:n]
