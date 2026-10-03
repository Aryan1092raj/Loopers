"""Concept graph seam (PLAN §4.2), consumed by learner, assess and the API.

Phase 1 stub: serves the hand-made fixture graph. A3 replaces the loader with
reads from the `concepts` and `concept_edges` tables; the signatures stay.
"""
import json
from functools import lru_cache
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "concept_graph.json"


@lru_cache(maxsize=1)
def get_graph() -> dict:
    """{"nodes": [{id, name, level, parent_id, aliases}], "edges": [{src, dst, weight}]}"""
    return json.loads(FIXTURE.read_text())


def get_concept(concept_id: str) -> dict | None:
    return next((n for n in get_graph()["nodes"] if n["id"] == concept_id), None)


def prerequisites(concept_id: str) -> list[str]:
    """Direct prerequisites: every src with an edge into concept_id."""
    return [e["src"] for e in get_graph()["edges"] if e["dst"] == concept_id]


def dependents(concept_id: str) -> list[str]:
    return [e["dst"] for e in get_graph()["edges"] if e["src"] == concept_id]


def concepts_for_scope(scope: dict | None) -> list[str]:
    """Concept ids under scope["topic_ids"] plus scope["concept_ids"]; empty scope means all."""
    nodes = [n for n in get_graph()["nodes"] if n["level"] != "topic"]
    topic_ids = set((scope or {}).get("topic_ids") or [])
    concept_ids = set((scope or {}).get("concept_ids") or [])
    if not topic_ids and not concept_ids:
        return [n["id"] for n in nodes]
    return [n["id"] for n in nodes if n["parent_id"] in topic_ids or n["id"] in concept_ids]


def tag_for_text(text: str) -> list[str]:
    # ponytail: name/alias substring match; A3 swaps in embedding cosine >= 0.45 (TRD §7.2)
    low = text.lower()
    return [
        n["id"] for n in get_graph()["nodes"]
        if n["level"] != "topic" and any(t.lower() in low for t in [n["name"], *n["aliases"]])
    ]
