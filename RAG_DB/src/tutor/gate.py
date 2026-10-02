"""Relevance gate (TRD §7.3). Phase 1: score bands only; Tier 2 NLI arrives with A5."""
import json
import os
from pathlib import Path

from core.models import GateResult

PLACEHOLDER = {"tau_low": 0.15, "tau_high": 0.45}  # until C's calibration writes gate_thresholds.json


def thresholds() -> dict:
    path = Path(os.getenv("GATE_THRESHOLDS", Path(__file__).resolve().parents[2] / "metadata" / "gate_thresholds.json"))
    return json.loads(path.read_text()) if path.is_file() else PLACEHOLDER


def decide(top_score: float) -> GateResult:
    t = thresholds()
    if top_score < t["tau_low"]:
        return GateResult(top_score=top_score, band="low", decision="refuse")
    band = "high" if top_score >= t["tau_high"] else "borderline"
    # ponytail: borderline answers without Tier 2 for now; A5 adds the NLI check and tier=2
    return GateResult(top_score=top_score, band=band, decision="answer")
