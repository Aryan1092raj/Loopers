"""Concept graph seam (PLAN §4.2), consumed by learner, assess and the API.

Serves the `concepts` and `concept_edges` tables built by concepts/build.py (A3). Until a build has
run those tables are empty and the hand-made fixture graph is served instead; the signatures are the same.
"""
import json
import sqlite3
from functools import lru_cache
from pathlib import Path

from core.db import get_connection

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "concept_graph.json"


@lru_cache(maxsize=1)
def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _from_db() -> dict | None:
    try:
        conn = get_connection()
        try:
            nodes = [
                {"id": r[0], "name": r[1], "level": r[2], "parent_id": r[3], "aliases": json.loads(r[4]), "description": r[5] or ""}
                for r in conn.execute("SELECT concept_id, name, level, parent_id, aliases_json, description FROM concepts")
            ]
            edges = [{"src": s, "dst": d, "weight": w} for s, d, w in conn.execute("SELECT src, dst, weight FROM concept_edges")]
        finally:
            conn.close()
    except sqlite3.OperationalError:  # tables not created yet
        return None
    return {"nodes": nodes, "edges": edges} if nodes else None


def get_graph(fixture: bool = True) -> dict:
    """{"nodes": [{id, name, level, parent_id, aliases}], "edges": [{src, dst, weight}]}

    fixture=False returns an empty graph, not the fixture, when no build has run."""
    return _from_db() or (_fixture() if fixture else {"nodes": [], "edges": []})


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


@lru_cache(maxsize=4)
def _concept_vectors(texts: tuple):
    from concepts.cluster import unit_vectors
    from embeddings import encode_hybrid

    return unit_vectors(encode_hybrid(list(texts))[0])


def tag_for_text(text: str) -> list[str]:
    """Concept ids for a text: the top 3 concepts with embedding cosine >= 0.45 (TRD §7.2)."""
    from concepts.tag import THRESHOLD, TOP

    graph = _from_db()
    if graph is None:
        # ponytail: the fixture graph has no embeddings; name/alias substring match
        low = text.lower()
        return [
            n["id"] for n in _fixture()["nodes"]
            if n["level"] != "topic" and any(t.lower() in low for t in [n["name"], *n["aliases"]])
        ]
    from concepts.cluster import unit_vectors
    from embeddings import encode_hybrid

    nodes = [n for n in graph["nodes"] if n["level"] != "topic"]
    sims = _concept_vectors(tuple(f"{n['name']}. {n['description']}" for n in nodes)) @ unit_vectors(encode_hybrid(text)[0])[0]
    best = sorted(range(len(nodes)), key=lambda i: -sims[i])[:TOP]
    return [nodes[i]["id"] for i in best if sims[i] >= THRESHOLD]
