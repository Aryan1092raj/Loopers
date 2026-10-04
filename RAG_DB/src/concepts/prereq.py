"""Prerequisite edges between concepts (TRD §7.2).

Candidates are pairs that co-occur in the same or adjacent windows, plus knowledge_graph triples of
weight 2 or more. Score = 0.4 * precedence + 0.3 * normalised triple weight + 0.3 * LLM entailment; keep >= 0.5.
"""
import json
import re
from collections import Counter, defaultdict

MIN_COOC = 2        # windows in which a pair must co-occur to become a candidate
MAX_ENTAIL = 300    # candidates sent to the LLM, best pre-score first
BATCH = 10          # pairs per LLM call
KEEP = 0.5

PROMPT = (
    "For each numbered pair, decide whether concept A is a prerequisite of concept B: B cannot be understood "
    "without first understanding A. Return only a JSON array with one object per pair "
    '{{"pair": <number>, "prerequisite": true or false, "why": "<the dependency in a short phrase>"}}.\n\n{pairs}'
)


def cooccurrence(mentions: list[dict], owner: list[int]) -> Counter:
    """Windows in which each unordered concept pair appears together or in neighbouring windows."""
    by_pos = defaultdict(set)
    for m, c in zip(mentions, owner):
        by_pos[m["pos"]].add(c)
    counts = Counter()
    for pos, here in by_pos.items():
        counts.update({frozenset((a, b)) for a in here for b in here | by_pos.get(pos + 1, set()) if a != b})
    return counts


def triple_weights(concepts: list[dict]) -> dict[tuple[int, int], int]:
    """knowledge_graph triples (weight >= 2) mapped onto concept indexes by name or alias."""
    try:
        from knowledge_graph import build_graph

        graph = build_graph()
    except Exception:
        return {}
    by_name = {n.lower(): i for i, c in enumerate(concepts) for n in [c["name"], *c["aliases"]]}
    out = {}
    for u, v, data in graph.edges(data=True):
        if data.get("weight", 1) >= 2 and u in by_name and v in by_name and by_name[u] != by_name[v]:
            key = (by_name[u], by_name[v])
            out[key] = max(out.get(key, 0), data["weight"])
    return out


def _entail(batch: list[dict], concepts: list[dict], llm) -> list[tuple[bool, str]]:
    pairs = "\n".join(f"{i}. A = {concepts[r['a']]['name']}; B = {concepts[r['b']]['name']}" for i, r in enumerate(batch, 1))
    found = re.search(r"\[.*\]", llm(PROMPT.format(pairs=pairs)), re.S)
    try:
        items = json.loads(found.group(0)) if found else []
    except json.JSONDecodeError:
        items = []
    verdicts = {i.get("pair"): (bool(i.get("prerequisite")), str(i.get("why") or "").strip()) for i in items if isinstance(i, dict)}
    return [verdicts.get(n, (False, "")) for n in range(1, len(batch) + 1)]


def score_edges(concepts: list[dict], mentions: list[dict], owner: list[int], llm,
                triples: dict | None = None, min_cooc: int = MIN_COOC) -> list[tuple[str, str, float, str]]:
    """Edges (src, dst, score, why) where src is a prerequisite of dst. May still contain cycles (see dag.py)."""
    triples = triple_weights(concepts) if triples is None else triples
    top = max(triples.values(), default=1)
    cands = {}
    for pair, n in cooccurrence(mentions, owner).items():
        if n >= min_cooc:
            a, b = sorted(pair, key=lambda i: concepts[i]["first_pos"])  # the concept met first is the candidate prerequisite
            cands[(a, b)] = n
    for pair in triples:
        cands.setdefault(pair, 0)
    rows = []
    for (a, b), n in cands.items():
        precedence = 1.0 if concepts[a]["first_pos"] < concepts[b]["first_pos"] else 0.0
        pre = 0.4 * precedence + 0.3 * min(triples.get((a, b), 0) / top, 1.0)
        if pre > 0:
            rows.append({"a": a, "b": b, "pre": pre, "n": n})
    rows = sorted(rows, key=lambda r: (-r["pre"], -r["n"]))[:MAX_ENTAIL]
    edges = []
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        for r, (needs, why) in zip(batch, _entail(batch, concepts, llm)):
            score = r["pre"] + (0.3 if needs and why else 0.0)
            if score >= KEEP:
                edges.append((concepts[r["a"]]["id"], concepts[r["b"]]["id"], round(score, 3), why))
    return edges
