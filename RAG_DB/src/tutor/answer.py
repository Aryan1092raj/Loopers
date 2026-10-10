"""Grounded tutor answers (TRD §7.4) and the whole /api/chat flow.

retrieve -> gate (Tier 1, Tier 2) -> generate with [E1] markers -> validate markers -> repair once ->
drop what still fails -> resolve markers to stored citations. The model writes markers only; page
numbers and timestamps come from `tutor.cite`.
"""
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

SYSTEM = (
    "You are a study tutor. Answer the student's question using ONLY the numbered evidence. "
    "End every factual sentence with one or more evidence markers such as [E1] or [E1][E3]. "
    "Never write page numbers, slide numbers or timestamps, and never invent a marker. "
    "If the evidence only partly answers the question, say so plainly. Evidence marked background is "
    "prerequisite material: use it to explain, not to answer. Reply in the language with code {lang}."
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
    order = ["deepseek", "groq", "gemini"] if role == "answer" else ["groq", "gemini", "deepseek"]
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


def _generate(question: str, evidence: list[Evidence], history: list[dict], lang: str) -> str:
    user = f"Evidence:\n{_evidence_block(evidence)}\n\nQuestion: {question}"
    return complete([{"role": "system", "content": SYSTEM.format(lang=lang)}, *_turns(history), {"role": "user", "content": user}])


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


def _grounded(question: str, evidence: list[Evidence], history: list[dict], lang: str, gate) -> TutorResponse:
    ids = {e.id for e in evidence}
    try:
        sentences = split_sentences(_generate(question, evidence, history, lang))
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


def respond(message: str, student_id: str, session_id: str, scope: dict | None = None,
            lang: str = "en", allow_outside: bool = False) -> TutorResponse:
    history = session.history(session_id)
    evidence, _ = retrieve_evidence(message, scope)
    gate = decide(top_score(evidence))
    if gate.band == "borderline":
        try:
            gate = tier2(gate, message, evidence, complete)
        except NoProvider:
            pass  # entailment cannot be checked without an LLM; keep the score-band decision
    if gate.decision == "refuse":
        resp = _outside(message, history, lang, gate) if allow_outside else _refused(gate, "not_entailed" if gate.nli else "out_of_material")
    else:
        resp = _grounded(message, evidence, history, lang, gate)
    if gate.decision == "answer":
        resp.mastery_updates = _observe(student_id, message, evidence)
    session.add_turn(session_id, student_id, "user", message)
    session.add_turn(session_id, student_id, "assistant", resp.answer or resp.refusal.message, resp.mode,
                     [c.model_dump() for c in resp.citations], gate.model_dump())
    return resp
