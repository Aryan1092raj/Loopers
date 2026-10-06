"""Knowledge Graph — prerequisite concept tracking (Phase 6 & 3 Bridge).

Extracts entity-relation triples from text chunks via LLM, stores them as a
directed prerequisite graph in SQLite + in-memory NetworkX.

M3.1 ADAPTER: Now bridged to write directly into Track B's `course.db`
(`concepts` and `concept_edges` tables) rather than a standalone `triples` DB.

Usage:
    from knowledge_graph import build_graph, get_prerequisites, extract_and_add

    G = build_graph()                  # load from SQLite
    prereqs = get_prerequisites(G, "lagrangian_mechanics", depth=2)
    extract_and_add(chunk_text)     # call during embed
"""
import json
import sqlite3
import re
from pathlib import Path
from config import METADATA

# Deprecated but kept to prevent breaking Track A import references
DB_PATH = METADATA / "knowledge_graph.db"

PREREQUISITE_RELATIONS = {
    "is_prerequisite_for",
    "requires",
    "is_required_for",
    "must_precede",
    "is_foundation_of",
    "enables",
}

_TRIPLE_SYSTEM = """You are an educational knowledge graph builder. Given a text chunk, extract concept-relation-concept triples. Focus on prerequisite/foundational relationships.

Return a JSON array of triples:
[
  {"subject": "concept A", "relation": "is_prerequisite_for", "object": "concept B"},
  ...
]

Use relations from: is_prerequisite_for, requires, is_part_of, leads_to, explains, defines, is_example_of.
Keep concept names short (1-4 words). Return [] if no clear relationships exist.
Return ONLY the JSON array."""

def _slugify(text: str) -> str:
    """Helper to convert raw text subjects into normalized concept_ids."""
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')

def _init_db(db_path: Path = None) -> sqlite3.Connection:
    """Bridged to return course.db connection instead of knowledge_graph.db"""
    from core.db import get_connection
    return get_connection()

def build_graph(db_path: Path | None = None) -> "networkx.DiGraph":
    """Load concepts and edges from course.db and return a NetworkX directed graph."""
    try:
        import networkx as nx
    except ImportError:
        raise ImportError("networkx is required: pip install networkx")

    G = nx.DiGraph()
    conn = _init_db()
    try:
        for row in conn.execute("SELECT src, dst, weight, evidence FROM concept_edges"):
            src, dst, weight, evidence = row
            rel = "requires"
            if evidence:
                try:
                    ev_data = json.loads(evidence)
                    if isinstance(ev_data, dict) and "relation" in ev_data:
                        rel = ev_data["relation"]
                except:
                    pass
            G.add_edge(src.lower(), dst.lower(), relation=rel, weight=float(weight or 1.0))
    except sqlite3.OperationalError:
        pass # Table might not exist if DB not initialized
    finally:
        conn.close()
    return G

def get_prerequisites(
    G: "networkx.DiGraph",
    concept: str,
    depth: int = 2,
    top_k: int = 6,
    min_weight: int = 2,
    decay: float = 0.7,
) -> list[str]:
    """PathRAG-style weighted BFS: returns top-k reliable prerequisite paths."""
    try:
        import networkx as nx
    except ImportError:
        return []

    concept_lower = concept.lower()
    if concept_lower not in G:
        return []

    scored: dict[str, float] = {}
    queue: list[tuple[str, int, float]] = [(concept_lower, 0, float("inf"))]
    while queue:
        node, d, path_w = queue.pop(0)
        if d >= depth:
            continue
        for pred in G.predecessors(node):
            edge_w = G[pred][node].get("weight", 1.0)
            if edge_w < min_weight:
                continue
            new_path_w = min(path_w, edge_w)
            score = new_path_w * (decay ** (d + 1))
            if pred not in scored or scored[pred] < score:
                scored[pred] = score
                queue.append((pred, d + 1, new_path_w))

    scored.pop(concept_lower, None)
    return [c for c, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:top_k]]

def extract_triples(chunk_text: str) -> list[dict]:
    """Call LLM to extract concept-relation triples from a chunk."""
    try:
        from llm import call_gemini, call_groq
        messages = [
            {"role": "system", "content": _TRIPLE_SYSTEM},
            {"role": "user", "content": chunk_text[:2000]},
        ]
        try:
            raw = call_gemini(messages, model="gemini-3.8-flash", max_tokens=512)
        except Exception:
            raw = call_groq(messages, max_tokens=512)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1].removeprefix("json").strip()
        data = json.loads(raw)
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)
                    and "subject" in t and "relation" in t and "object" in t]
    except Exception:
        pass
    return []

def add_triples(triples: list[dict], db_path: Path | None = None) -> None:
    """Bridged persistence: maps triples into course.db concepts & concept_edges."""
    if not triples:
        return
    conn = _init_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for t in triples:
            subj_id = _slugify(t.get("subject", ""))
            obj_id = _slugify(t.get("object", ""))
            if not subj_id or not obj_id:
                continue
                
            rel = t.get("relation", "requires")
            
            # Upsert nodes
            conn.execute('''
                INSERT INTO concepts (concept_id, name, level) 
                VALUES (?, ?, 'concept')
                ON CONFLICT(concept_id) DO NOTHING
            ''', (subj_id, t.get("subject", subj_id)))
            
            conn.execute('''
                INSERT INTO concepts (concept_id, name, level) 
                VALUES (?, ?, 'concept')
                ON CONFLICT(concept_id) DO NOTHING
            ''', (obj_id, t.get("object", obj_id)))
            
            # Upsert edge
            evidence_json = json.dumps({"relation": rel})
            conn.execute('''
                INSERT INTO concept_edges (src, dst, weight, evidence)
                VALUES (?, ?, 1.0, ?)
                ON CONFLICT(src, dst) DO UPDATE SET 
                    weight = weight + 1.0,
                    evidence = ?
            ''', (subj_id, obj_id, evidence_json, evidence_json))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[Adapter] Error writing triples to course.db: {e}")
    finally:
        conn.close()

def extract_and_add(chunk_text: str, db_path: Path | None = None) -> list[dict]:
    triples = extract_triples(chunk_text)
    add_triples(triples, db_path)
    return triples
