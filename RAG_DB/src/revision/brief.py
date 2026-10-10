"""Short revision brief (PS 6b) for the weakest concepts: a cited script the client reads aloud.

`audio_url` stays None: the client speaks `text` with browser speech synthesis (TRD §6.6 allows it).
"""
from revision.flashcards import evidence_block, evidence_for, weakest
from tutor.answer import NoProvider, _segments, complete
from tutor.cite import MARKER, body, citations_for, sentence_ok, split_sentences

UNITS_PER_CONCEPT = 2
EXTRACT_CHARS = 300  # per unit when no LLM is configured

SYSTEM = (
    "You write a 30-second spoken revision brief for a student, covering the listed concepts. Use ONLY the "
    "numbered evidence. Write plain sentences, no lists. End every factual sentence with one or more evidence "
    "markers such as [E1] or [E1][E3]. Never write page numbers, slide numbers or timestamps. "
    "Reply in the language with code {lang}."
)


def brief(student_id: str, concept_ids: list[str] | None = None, lang: str = "en", n_concepts: int = 3) -> dict:
    concepts = weakest(student_id, len(concept_ids) if concept_ids else n_concepts, concept_ids)
    evidence = []
    for cid, _ in concepts:
        evidence += evidence_for(cid, start=len(evidence) + 1, limit=UNITS_PER_CONCEPT)
    if not evidence:
        return {"text": "", "segments": [], "citations": [], "audio_url": None}
    ids = {e.id for e in evidence}
    names = ", ".join(name for _, name in concepts)
    try:
        sentences = split_sentences(complete(
            [{"role": "system", "content": SYSTEM.format(lang=lang)},
             {"role": "user", "content": f"Concepts: {names}\n\nEvidence:\n{evidence_block(evidence)}"}],
        ))
    except NoProvider:
        sentences = [f"{body(e.unit.text, e.unit.heading)[:EXTRACT_CHARS].strip()} [{e.id}]" for e in evidence]
    # a sentence with a bad marker is dropped, not repaired: a brief is short and regenerated on demand
    sentences = [s for s in sentences if MARKER.search(s) and sentence_ok(s, ids)]
    segments = _segments(sentences)
    used = {eid for seg in segments for eid in seg.evidence_ids}
    return {
        "text": ". ".join(seg.text for seg in segments) + ("." if segments else ""),
        "segments": segments,
        "citations": citations_for([e for e in evidence if e.id in used]),
        "audio_url": None,
    }
