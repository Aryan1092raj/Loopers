"""Cost logger for RAG_DB generation runs — mirrors src/lib/cost.ts in the main
LMS app so both halves of the project report cost the same way (same pricing
table, same USD->INR rate, same COST-*.json shape idea).

A single "generate"/"notes"/"assignment" run can fire several parallel LLM
calls (content writer per slide group, TikZ writer per diagram, judge/eval).
record() is called once per LLM call from anywhere in the call stack; reset()
clears the accumulator before a run starts; write_log() sums everything up and
writes one COST-*.json for the whole run, same as the main app does per lecture.
"""
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

USD_TO_INR = 86  # matches src/lib/cost.ts — dev-phase estimate, not billing-accurate

# $ per million tokens (in, out). Mirrors PRICES_PER_MTOK in src/lib/claude.ts —
# keep these two tables in sync if pricing changes.
PRICES_PER_MTOK = {
    "claude-opus-4-8": (5, 25),
    "claude-opus-4-7": (5, 25),
    "claude-opus-4-6": (5, 25),
    "claude-opus-4-5": (5, 25),
    "claude-sonnet-5": (3, 15),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    "claude-fable-5": (10, 50),
    # Free-tier fallbacks — no charge to log.
    "gemini-2.5-flash": (0, 0),
    "gemini-2.0-flash": (0, 0),
    "gemini-1.5-flash": (0, 0),
    # DeepSeek V4 Pro (75% promo rate through 2026-05-31)
    "deepseek-v4-pro": (0.435, 0.87),
    # DeepSeek V4 Flash. Listed peak rate; off-peak (10:00-01:00 and 04:00-06:00 UTC)
    # is half this, so a logged cost is an upper bound, never an under-report.
    "deepseek-v4-flash": (0.44, 1.32),
}

_lock = threading.Lock()
_calls: list[dict] = []
_run_started_at: float | None = None


def reset() -> None:
    """Clear the accumulator and mark the start of a new run."""
    global _run_started_at
    with _lock:
        _calls.clear()
        _run_started_at = time.time()


def record(provider: str, model: str, input_tokens: int, output_tokens: int, role: str = "") -> None:
    """Record one LLM call's token usage. Thread-safe — call from any worker thread."""
    with _lock:
        _calls.append({
            "provider": provider,
            "model": model,
            "role": role,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })


def _cost_for_call(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, (0, 0))
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def summary() -> dict:
    """Aggregate all recorded calls into one totals dict (thread-safe snapshot)."""
    with _lock:
        calls = list(_calls)
        started_at = _run_started_at

    total_cost_usd = 0.0
    total_in = 0
    total_out = 0
    by_role: dict[str, dict] = {}

    for c in calls:
        cost = _cost_for_call(c["model"], c["input_tokens"], c["output_tokens"])
        total_cost_usd += cost
        total_in += c["input_tokens"]
        total_out += c["output_tokens"]
        role = c["role"] or "unspecified"
        bucket = by_role.setdefault(role, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        bucket["calls"] += 1
        bucket["input_tokens"] += c["input_tokens"]
        bucket["output_tokens"] += c["output_tokens"]
        bucket["cost_usd"] += cost

    elapsed = (time.time() - started_at) if started_at else 0.0

    # Build per-call details for comparison table
    call_details = []
    for c in calls:
        cost = _cost_for_call(c["model"], c["input_tokens"], c["output_tokens"])
        call_details.append({
            "provider": c["provider"],
            "model": c["model"],
            "role": c["role"],
            "inputTokens": c["input_tokens"],
            "outputTokens": c["output_tokens"],
            "costUsd": round(cost, 6),
            "costInr": round(cost * USD_TO_INR, 2),
        })

    return {
        "calls": len(calls),
        "totalInputTokens": total_in,
        "totalOutputTokens": total_out,
        "totalCostUsd": round(total_cost_usd, 6),
        "totalCostInr": round(total_cost_usd * USD_TO_INR, 2),
        "byRole": {
            role: {
                "calls": b["calls"],
                "inputTokens": b["input_tokens"],
                "outputTokens": b["output_tokens"],
                "costUsd": round(b["cost_usd"], 6),
                "costInr": round(b["cost_usd"] * USD_TO_INR, 2),
            }
            for role, b in by_role.items()
        },
        "callDetails": call_details,
        "elapsedSec": round(elapsed, 1),
    }


def _slugify(s: str, max_len: int = 48) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:max_len].rstrip("-")
    return slug or "untitled"


def cost_log_dir() -> Path:
    return ROOT / "metadata"


def cost_logging_on() -> bool:
    flag = os.getenv("RAG_COST_LOG", "").lower()
    if flag in ("true", "1"):
        return True
    if flag in ("false", "0"):
        return False
    return True  # on by default — this is the whole point of the dev-phase logger


def write_log(phase: str, topic: str, subject: str | None, ok: bool, error: str | None = None) -> dict:
    """Write one COST-*.json for the whole run and return the summary dict."""
    s = summary()
    record_obj = {
        "run": {
            "phase": phase,
            "topic": topic,
            "subject": subject or None,
            "ok": ok,
            "error": error,
            "elapsedSec": s["elapsedSec"],
        },
        "tokens": {
            "totalInput": s["totalInputTokens"],
            "totalOutput": s["totalOutputTokens"],
        },
        "byRole": s["byRole"],
        "callDetails": s["callDetails"],
        "totalCost": {
            "usd": s["totalCostUsd"],
            "inr": s["totalCostInr"],
        },
        "usdToInr": USD_TO_INR,
    }

    if cost_logging_on():
        try:
            d = cost_log_dir()
            d.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            fname = f"COST-{phase}-{_slugify(topic)}-{ts}.json"
            (d / fname).write_text(json.dumps(record_obj, indent=2), encoding="utf-8")
        except Exception:
            pass  # never let logging failures break the actual generation run

    return record_obj
