"""Relevance gate (TRD §7.3). Tier 1 bands the top reranker score; Tier 2 asks an NLI question about borderline scores."""
import json
import os
import re
from pathlib import Path

from core.models import Evidence, GateResult

PLACEHOLDER = {"tau_low": 0.15, "tau_high": 0.45}  # until C's calibration writes gate_thresholds.json
NLI_LABELS = ("ENTAILMENT", "NEUTRAL", "CONTRADICTION")
PREMISE_CHARS = 1500  # per evidence unit in the Tier 2 premise


def thresholds() -> dict:
    path = Path(os.getenv("GATE_THRESHOLDS", Path(__file__).resolve().parents[2] / "metadata" / "gate_thresholds.json"))
    return json.loads(path.read_text()) if path.is_file() else PLACEHOLDER


def top_score(evidence: list[Evidence]) -> float:
    """Gate on reranked text scores only; figure and prerequisite evidence carry other score scales."""
    return max((e.score for e in evidence if e.unit.kind != "figure" and not e.is_prerequisite), default=0.0)


def decide(top: float) -> GateResult:
    t = thresholds()
    if top < t["tau_low"]:
        return GateResult(top_score=top, band="low", decision="refuse")
    if top >= t["tau_high"]:
        return GateResult(top_score=top, band="high", decision="answer")
    # borderline: Tier 2 must confirm; the decision stays "answer" until tier2() says otherwise
    return GateResult(top_score=top, band="borderline", tier=2, decision="answer")


def _parse_label(text: str) -> str:
    found = re.search("|".join(NLI_LABELS), text.upper())
    return found.group(0) if found else "NEUTRAL"


def tier2(gate: GateResult, question: str, evidence: list[Evidence], complete) -> GateResult:
    """Draft a one-sentence answer from the evidence, then ask whether the evidence entails it.

    `complete(messages, role)` is the LLM call (tutor.answer.complete). Only ENTAILMENT keeps the answer.
    """
    premise = "\n\n".join(e.unit.text[:PREMISE_CHARS] for e in evidence if e.unit.kind != "figure")
    draft = complete([{
        "role": "user",
        "content": f"Evidence:\n{premise}\n\nQuestion: {question}\n\n"
                   "Write a one-sentence draft answer using only the evidence. Reply with the sentence only.",
    }], role="gate").strip()
    verdict = complete([{
        "role": "user",
        "content": f"Premise:\n{premise}\n\nHypothesis: {draft}\n\n"
                   "Does the premise entail the hypothesis? Reply with exactly one word: "
                   "ENTAILMENT, NEUTRAL or CONTRADICTION.",
    }], role="gate")
    label = _parse_label(verdict)
    return gate.model_copy(update={"nli": label, "decision": "answer" if label == "ENTAILMENT" else "refuse"})
