"""Custom evaluation metrics (TRD §8): hit@k, MRR, citation accuracy, refusal honesty, over-refusal, gate calibration.

Everything here is pure: no LLM, no Qdrant. `run.py` feeds it retrieved locators and gate scores.
"""


def matches(source_id: str, locator: dict, gold: dict) -> bool:
    """True when a unit's stored locator lands on a gold locator (same source, same page/slide, or timestamp inside the span)."""
    if gold.get("source_id") != source_id:
        return False
    for key in ("page_no", "slide_no"):
        if gold.get(key) is not None:
            return locator.get(key) == gold[key]
    t = gold.get("t_start")
    if t is not None:
        start = locator.get("t_start")
        end = locator.get("t_end")
        return start is not None and start <= t <= (end if end is not None else start)
    return True


def first_hit_rank(units: list[tuple[str, dict]], golds: list[dict]) -> int | None:
    """1-based rank of the first (source_id, locator) unit that matches any gold locator."""
    for rank, (sid, loc) in enumerate(units, 1):
        if any(matches(sid, loc, g) for g in golds):
            return rank
    return None


def retrieval_metrics(ranks: list[int | None], k: int) -> dict:
    n = len(ranks) or 1
    return {
        f"hit@{k}": sum(r is not None and r <= k for r in ranks) / n,
        "mrr": sum(1 / r for r in ranks if r) / n,
    }


def citation_accuracy(cited: list[list[tuple[str, dict]]], golds: list[list[dict]]) -> float:
    """Share of all citations (over answered items) whose unit overlaps a gold locator of its item."""
    total = good = 0
    for units, gold in zip(cited, golds):
        for sid, loc in units:
            total += 1
            good += any(matches(sid, loc, g) for g in gold)
    return good / total if total else 0.0


def refusal_rates(in_scope_refused: list[bool], off_refused: list[bool]) -> dict:
    return {
        "refusal_honesty": sum(off_refused) / len(off_refused) if off_refused else 0.0,
        "over_refusal": sum(in_scope_refused) / len(in_scope_refused) if in_scope_refused else 0.0,
    }


def calibrate_gate(in_scores: list[float], off_scores: list[float], honesty_min: float = 0.95, over_max: float = 0.05) -> dict:
    """Pick gate thresholds from top reranker scores (TRD §7.3).

    tau_low: smallest value with honesty >= honesty_min and over-refusal <= over_max, else the Youden's J maximiser
    (honesty - over_refusal). tau_high: 5th percentile of in-scope scores, so 95% of in-scope questions skip Tier 2.
    """
    if not in_scores or not off_scores:
        raise ValueError("calibration needs both in-scope and off-material scores")

    def rates(tau: float) -> tuple[float, float]:
        return sum(s < tau for s in off_scores) / len(off_scores), sum(s < tau for s in in_scores) / len(in_scores)

    candidates = sorted({0.0, *in_scores, *off_scores, max(in_scores + off_scores) + 1e-6})
    ok = [t for t in candidates if rates(t)[0] >= honesty_min and rates(t)[1] <= over_max]
    tau_low = ok[0] if ok else max(candidates, key=lambda t: rates(t)[0] - rates(t)[1])
    honesty, over = rates(tau_low)
    ranked = sorted(in_scores)
    tau_high = max(tau_low, ranked[int(0.05 * len(ranked))])
    return {"tau_low": round(tau_low, 4), "tau_high": round(tau_high, 4), "refusal_honesty": honesty,
            "over_refusal": over, "targets_met": bool(ok)}
