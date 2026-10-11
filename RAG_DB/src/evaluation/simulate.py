"""Simulated-student evaluation of personalization (PS 5c, TRD §8).

    cd RAG_DB/src
    python -m evaluation.simulate --students 20 --sessions 10 --items 10

Parametric students (fast, average and slow profiles) practise the stored concept graph over several sessions, once under the adaptive policy
and once under a control policy, with the same students in both arms. Adaptive uses the same BKT update as
`learner/evidence.py` to pick the weakest concept whose prerequisites look mastered, and never re-serves an item.
Control picks a random concept and a random item with replacement, like a fixed quiz bank. No LLM and no database is
involved except the final `eval_runs` row, so the run is cheap and repeatable.

Reported: mean true mastery gain per arm, the paired adaptive-minus-control difference with its standard error,
the share of served items a student had already seen, and how closely the BKT estimate tracks true mastery after
each session (the learner model should get closer as evidence accumulates).
Limits: the student model is an assumption (learning rate, slip, guess, forgetting), so the numbers show the policy
effect under that model, not real learners. LLM-simulated students are not run.
"""
import argparse
import json
import math
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from learner.bkt import update_p_know
from learner.fsrs import retrievability

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "concept_graph.json"
META = Path(__file__).resolve().parents[2] / "metadata"
BANK = 12          # items per concept
GAP_DAYS = 2.0     # days between sessions (forgetting applies)
P_SLIP, P_GUESS, P_TRANS = 0.1, 0.2, 0.05  # BKT parameters used by learner/evidence.py
READY = 0.6        # adaptive picks a concept only when every prerequisite has p_know above this
PROFILES = {       # learn rate, slip, guess, memory stability in days
    "fast": dict(rate=0.25, slip=0.05, guess=0.15, stability=10.0),
    "average": dict(rate=0.15, slip=0.10, guess=0.20, stability=6.0),
    "slow": dict(rate=0.08, slip=0.15, guess=0.25, stability=3.0),
}


def load_graph() -> tuple[list[str], dict[str, list[str]]]:
    """Leaf concepts and their prerequisites: the stored concept graph, else the fixture graph (no corpus ingested yet)."""
    from core.db import get_connection

    conn = get_connection()
    try:
        ids = [r[0] for r in conn.execute("SELECT concept_id FROM concepts WHERE level != 'topic'")]
        edges = [(r[0], r[1]) for r in conn.execute("SELECT src, dst FROM concept_edges")]
    finally:
        conn.close()
    if len(ids) < 3:
        g = json.loads(FIXTURE.read_text())
        ids = [n["id"] for n in g["nodes"] if n["level"] != "topic"]
        edges = [(e["src"], e["dst"]) for e in g["edges"]]
    prereqs = {c: [] for c in ids}
    for src, dst in edges:
        if src in prereqs and dst in prereqs:
            prereqs[dst].append(src)
    return ids, prereqs


def run_student(seed: int, profile: dict, policy: str, sessions: int, items: int, concepts: list[str], prereqs: dict) -> dict:
    init = random.Random(seed)  # same starting mastery in both arms
    true = {c: init.uniform(0.05, 0.3) for c in concepts}
    rng = random.Random(seed * 2 + (policy == "adaptive"))
    est = {c: 0.15 for c in concepts}
    seen = {c: set() for c in concepts}
    served = repeats = 0
    curve = [sum(true.values()) / len(concepts)]
    mae = lambda: sum(abs(est[c] - true[c]) for c in concepts) / len(concepts)
    mae_curve = [mae()]  # how far the learner model is from the truth, after each session
    for _ in range(sessions):
        for c in concepts:  # forgetting since the last session
            true[c] *= retrievability(GAP_DAYS, profile["stability"])
        for _ in range(items):
            if policy == "adaptive":
                ready = [c for c in concepts if all(est[p] >= READY for p in prereqs[c])] or concepts
                c = min(ready, key=lambda x: est[x])
                fresh = [i for i in range(BANK) if i not in seen[c]]
                item = rng.choice(fresh) if fresh else rng.randrange(BANK)
            else:
                c, item = rng.choice(concepts), rng.randrange(BANK)
            served += 1
            repeats += item in seen[c]
            seen[c].add(item)
            correct = rng.random() < true[c] * (1 - profile["slip"]) + (1 - true[c]) * profile["guess"]
            est[c] = update_p_know(est[c], P_SLIP, P_GUESS, P_TRANS, correct)
            gate = 0.3 + 0.7 * min((true[p] for p in prereqs[c]), default=1.0)  # learning needs prerequisites
            true[c] += profile["rate"] * gate * (1 - true[c])
        curve.append(sum(true.values()) / len(concepts))
        mae_curve.append(mae())
    return {"curve": curve, "gain": curve[-1] - curve[0], "repetition": repeats / served, "estimate_mae": mae_curve[-1],
            "mae_curve": mae_curve}


def _mean(xs):
    return sum(xs) / len(xs)


def _se(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1) / len(xs))


def simulate(students: int = 20, sessions: int = 10, items: int = 10) -> dict:
    concepts, prereqs = load_graph()
    runs = {"adaptive": [], "control": []}
    for name, prof in PROFILES.items():
        for s in range(students):
            seed = list(PROFILES).index(name) * 1000 + s  # stable across runs, shared by both arms
            for policy in runs:
                runs[policy].append({"profile": name, **run_student(seed, prof, policy, sessions, items, concepts, prereqs)})
    out = {"students_per_profile": students, "sessions": sessions, "items_per_session": items, "concepts": len(concepts)}
    for policy, rs in runs.items():
        out[policy] = {
            "mean_gain": _mean([r["gain"] for r in rs]),
            "repetition_rate": _mean([r["repetition"] for r in rs]),
            "mean_curve": [_mean([r["curve"][t] for r in rs]) for t in range(sessions + 1)],
            "by_profile": {p: _mean([r["gain"] for r in rs if r["profile"] == p]) for p in PROFILES},
        }
    out["adaptive"]["estimate_mae"] = _mean([r["estimate_mae"] for r in runs["adaptive"]])
    out["adaptive"]["estimate_mae_curve"] = [_mean([r["mae_curve"][t] for r in runs["adaptive"]]) for t in range(sessions + 1)]
    diffs = [a["gain"] - c["gain"] for a, c in zip(runs["adaptive"], runs["control"])]  # paired by student
    out["paired_gain_difference"] = {"mean": _mean(diffs), "se": _se(diffs), "n": len(diffs),
                                     "students_where_adaptive_won": sum(d > 0 for d in diffs) / len(diffs)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=20, help="students per profile")
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--items", type=int, default=10, help="items per session")
    a = ap.parse_args()
    result = simulate(a.students, a.sessions, a.items)

    META.mkdir(exist_ok=True)
    (META / "simulation.json").write_text(json.dumps(result, indent=2))
    from core.db import get_connection

    conn = get_connection()
    try:
        conn.execute("INSERT INTO eval_runs(run_id, kind, config_json, metrics_json, created_at) VALUES (?,?,?,?,?)",
                     ("sim_" + uuid.uuid4().hex[:10], "simulation", json.dumps(vars(a)), json.dumps(result),
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({k: v for k, v in result.items() if k not in ("adaptive", "control")}, indent=2))
    for p in ("adaptive", "control"):
        r = result[p]
        print(f"{p:9} gain {r['mean_gain']:.3f}  repetition {r['repetition_rate']:.3f}  by profile "
              + " ".join(f"{k} {v:.3f}" for k, v in r["by_profile"].items()))
    print("BKT estimate MAE by session (adaptive): " + " ".join(f"{v:.3f}" for v in result["adaptive"]["estimate_mae_curve"]))


if __name__ == "__main__":
    main()
