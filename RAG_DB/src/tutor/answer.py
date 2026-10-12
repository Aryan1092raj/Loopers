"""Grounded tutor answers (TRD §7.4) and the whole /api/chat flow.

retrieve -> gate (Tier 1, Tier 2) -> generate with [E1] markers -> validate markers -> repair once ->
drop what still fails -> resolve markers to stored citations. The model writes markers only; page
numbers and timestamps come from `tutor.cite`.
"""
import json
import re

import config
from core.models import Evidence, Refusal, Segment, TutorResponse
from tutor import session
from tutor.cite import MARKER, body, citations_for, sentence_ok, split_sentences
from tutor.gate import decide, tier2, top_score
from tutor.retrieve import retrieve_evidence

REFUSAL_MESSAGE = "I could not find this in your course material."
NOT_ENTAILED_MESSAGE = "The material I found does not clearly answer this question."
EVIDENCE_CHARS = 1500  # per unit in the prompt
EXTRACT_CHARS = 300    # per unit when no LLM is configured
DEFAULT_P_KNOW = 0.15  # learner prior for a concept with no evidence yet
WEAK, STRONG = 0.4, 0.75

SYSTEM = (
    "You are a study tutor. Answer the student's question using ONLY the numbered evidence. "
    "End every factual sentence with one or more evidence markers such as [E1] or [E1][E3]. "
    "Never write page numbers, slide numbers or timestamps, and never invent a marker. "
    "If the evidence only partly answers the question, say so plainly. Evidence marked background is "
    "prerequisite material: use it to explain, not to answer. Reply in the language with code {lang}."
)
TRANSLATE_SYSTEM = (
    "Detect the language of the student's question; Hindi written in Latin letters (Hinglish) counts as hi. "
    "Reply with a single JSON object {\"lang\": \"<ISO 639-1 code>\", \"search\": \"<the subject question in English, "
    "without any request about the reply's language or format such as 'explain in Hindi'>\"} and nothing else."
)


class NoProvider(RuntimeError):
    """No configured LLM provider answered."""


def complete(messages: list[dict], role: str = "answer", max_tokens: int = 1200) -> str:
    """One LLM call with role routing (TRD §9): answers try DeepSeek, Groq, Gemini; gate calls try Groq first."""
    import llm

    providers = {
        "deepseek": (bool(config.DEEPSEEK_API_KEY), lambda m: llm.call_deepseek(messages, max_tokens=max_tokens, thinking_disabled=True, model=m)),
        "groq": (bool(config.GROQ_KEYS), lambda m: llm.call_groq(messages, max_tokens=max_tokens, model=m)),
        "gemini": (bool(config.GEMINI_KEYS), lambda m: llm.call_gemini(messages, max_tokens=max_tokens, **({"model": m} if m else {}))),
    }
    # concept extraction is bulk work, not judging, so it may share the generator's provider (CLAUDE.md rule 7)
    order = ["deepseek", "groq", "gemini"] if role in ("answer", "concepts") else ["groq", "gemini", "deepseek"]
    model = (config.GENERATOR_MODEL or None) if role == "answer" else None
    errors = []
    for name in order:
        configured, call = providers[name]
        if not configured:
            continue
        try:
            return call(model)
        except Exception as e:  # try the next provider
            errors.append(f"{name}: {e}")
        model = None  # GENERATOR_MODEL names one provider's model; fallbacks use their own defaults
    raise NoProvider("; ".join(errors) or "no LLM key is configured")


def _evidence_block(evidence: list[Evidence]) -> str:
    parts = []
    for e in evidence:
        head = f" - {e.unit.heading}" if e.unit.heading else ""
        tag = " (background)" if e.is_prerequisite else ""
        parts.append(f"[{e.id}] {e.unit.title}{head}{tag}\n{body(e.unit.text, e.unit.heading)[:EVIDENCE_CHARS]}")
    return "\n\n".join(parts)


def _turns(history: list[dict]) -> list[dict]:
    # earlier answers carry markers for evidence that is no longer in the prompt
    return [{"role": t["role"], "content": MARKER.sub("", t["content"])} for t in history]


def learner_note(student_id: str, evidence: list[Evidence]) -> str:
    """How deep to go, from the student's BKT mastery of the concepts tagged on the answering units."""
    from core.db import get_connection

    unit_ids = [e.unit.unit_id for e in evidence if not e.is_prerequisite]
    if not unit_ids:
        return ""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT c.name, COALESCE(ls.p_mastery, ?) FROM (SELECT DISTINCT concept_id FROM unit_concepts"
            f" WHERE unit_id IN ({','.join('?' * len(unit_ids))})) uc JOIN concepts c ON c.concept_id = uc.concept_id"
            " LEFT JOIN learner_state ls ON ls.concept_id = uc.concept_id AND ls.student_id = ?",
            (DEFAULT_P_KNOW, *unit_ids, student_id)).fetchall()
    finally:
        conn.close()
    if not rows:
        return ""
    p = sum(r[1] for r in rows) / len(rows)
    weak = [r[0] for r in rows if r[1] < WEAK][:3]
    if p < WEAK:
        return (f" The student is new to this (mastery {p:.2f}): explain step by step, define each term first, "
                "use the background evidence for missing basics, and end with one short check-your-understanding question."
                + (f" Weak concepts: {', '.join(weak)}." if weak else ""))
    if p >= STRONG:
        return f" The student already knows this well (mastery {p:.2f}): answer concisely and point out the subtle or exam-relevant detail."
    return f" The student partly knows this (mastery {p:.2f}): give a clear explanation with one worked example from the evidence."


def _generate(question: str, evidence: list[Evidence], history: list[dict], lang: str, note: str = "") -> str:
    user = f"Evidence:\n{_evidence_block(evidence)}\n\nQuestion: {question}"
    return complete([{"role": "system", "content": SYSTEM.format(lang=lang) + note}, *_turns(history), {"role": "user", "content": user}])


def _repair(sentences: list[str], evidence: list[Evidence]) -> list[str]:
    """Ask once for corrected sentences; [] if the call fails or the reply does not line up."""
    ids = ", ".join(f"[{e.id}]" for e in evidence)
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, 1))
    prompt = (
        f"Evidence:\n{_evidence_block(evidence)}\n\nValid markers: {ids}\n\n"
        "Each sentence below has no valid evidence marker. Rewrite each so that it ends with the marker(s) of the "
        "evidence that supports it, or write DROP if the evidence does not support it. "
        f"Return exactly {len(sentences)} lines, one per sentence, in order, nothing else.\n\n{numbered}"
    )
    try:
        reply = complete([{"role": "user", "content": prompt}])
    except NoProvider:
        return []
    return [re.sub(r"^\s*\d+[.)]\s*", "", line).strip() for line in reply.strip().splitlines() if line.strip()]


def _segments(sentences: list[str]) -> list[Segment]:
    return [
        Segment(text=re.sub(r"\s+", " ", MARKER.sub("", s)).strip().rstrip("."), evidence_ids=list(dict.fromkeys(MARKER.findall(s))))
        for s in sentences
    ]


def _refused(gate, reason: str) -> TutorResponse:
    message = REFUSAL_MESSAGE if reason == "out_of_material" else NOT_ENTAILED_MESSAGE
    return TutorResponse(
        mode="refused", answer="", segments=[], citations=[], gate=gate,
        refusal=Refusal(reason=reason, message=message, offer_outside=True),
    )


def _outside(question: str, history: list[dict], lang: str, gate) -> TutorResponse:
    system = (
        "You are a study tutor. The student's course material does not cover this question. Answer from general "
        "knowledge, concisely, starting with 'This is not from your course material.' Do not write evidence "
        f"markers. Reply in the language with code {lang}."
    )
    try:
        text = complete([{"role": "system", "content": system}, *_turns(history), {"role": "user", "content": question}])
    except NoProvider:
        return _refused(gate, "out_of_material")
    sentences = split_sentences(MARKER.sub("", text))
    if not sentences:
        return _refused(gate, "out_of_material")
    return TutorResponse(
        mode="outside_syllabus", answer=" ".join(sentences),
        segments=[Segment(text=s.rstrip("."), outside=True) for s in sentences], citations=[], gate=gate,
    )


def _grounded(question: str, evidence: list[Evidence], history: list[dict], lang: str, gate, note: str = "") -> TutorResponse:
    ids = {e.id for e in evidence}
    try:
        sentences = split_sentences(_generate(question, evidence, history, lang, note))
    except NoProvider:
        # no LLM configured: quote the best evidence so the API still returns cited, grounded text
        sentences = [
            f"{body(e.unit.text, e.unit.heading)[:EXTRACT_CHARS].strip()} [{e.id}]"
            for e in evidence if not e.is_prerequisite and e.unit.kind != "figure"
        ][:3]
    bad = [i for i, s in enumerate(sentences) if not sentence_ok(s, ids)]
    fixed = _repair([sentences[i] for i in bad], evidence) if bad else []
    dropped = 0
    for n, i in enumerate(bad):
        line = fixed[n] if len(fixed) == len(bad) else ""
        if line and line.upper() != "DROP" and sentence_ok(line, ids):
            sentences[i] = line
        else:
            sentences[i] = ""
            dropped += 1
    sentences = [s for s in sentences if s]
    if not sentences:
        return _refused(gate, "not_entailed")
    segments = _segments(sentences)
    used = {eid for seg in segments for eid in seg.evidence_ids}
    return TutorResponse(
        mode="partially_grounded" if dropped else "grounded", answer=" ".join(sentences), segments=segments,
        citations=citations_for([e for e in evidence if e.id in used]), gate=gate,
    )


def _observe(student_id: str, message: str, evidence: list[Evidence]) -> list:
    from learner.evidence import observe
    try:
        return observe(student_id, message, evidence)
    except Exception as e:  # mastery tracking must not break an answer
        print(f"[tutor] learner.evidence.observe failed: {e}")
        return []


def _to_english(message: str) -> tuple[str, str] | None:
    """(English question, language code) for a non-English question, else None. The reranker that scores
    evidence reads English only, so a Hindi or Hinglish question would otherwise be refused."""
    try:
        reply = complete([{"role": "system", "content": TRANSLATE_SYSTEM}, {"role": "user", "content": message}],
                         role="gate", max_tokens=300)
        found = json.loads(re.search(r"\{.*\}", reply, re.S).group(0))
    except (NoProvider, json.JSONDecodeError, AttributeError):
        return None
    lang, english = str(found.get("lang", "en")).lower(), str(found.get("search", "")).strip()
    return (english, lang) if lang != "en" and english else None


def _gate(query: str, evidence: list[Evidence]):
    gate = decide(top_score(evidence))
    if gate.band == "borderline":
        try:
            gate = tier2(gate, query, evidence, complete)
        except NoProvider:
            pass  # entailment cannot be checked without an LLM; keep the score-band decision
    return gate


def respond(message: str, student_id: str, session_id: str, scope: dict | None = None,
            lang: str = "en", allow_outside: bool = False) -> TutorResponse:
    history = session.history(session_id)
    query = message
    evidence, _ = retrieve_evidence(query, scope)
    gate = _gate(query, evidence)
    # Only a refused question pays for the translation call; an English one comes back as None.
    if gate.decision == "refuse" and (translated := _to_english(message)):
        query, asked_in = translated
        evidence, _ = retrieve_evidence(query, scope)
        gate = _gate(query, evidence)
        if lang == "en":  # answer in the language the student asked in
            lang = asked_in
    if gate.decision == "refuse":
        resp = _outside(message, history, lang, gate) if allow_outside else _refused(gate, "not_entailed" if gate.nli else "out_of_material")
    else:
        try:
            note = learner_note(student_id, evidence)
        except Exception as e:  # personalisation must not break an answer
            print(f"[tutor] learner_note failed: {e}")
            note = ""
        resp = _grounded(message, evidence, history, lang, gate, note)
    if gate.decision == "answer":
        resp.mastery_updates = _observe(student_id, query, evidence)
    session.add_turn(session_id, student_id, "user", message)
    session.add_turn(session_id, student_id, "assistant", resp.answer or resp.refusal.message, resp.mode,
                     [c.model_dump() for c in resp.citations], gate.model_dump())
    return resp
