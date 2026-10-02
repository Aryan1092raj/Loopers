"""Knowledge Graph — prerequisite concept tracking (Phase 6).

Extracts entity-relation triples from text chunks via LLM, stores them as a
directed prerequisite graph in SQLite + in-memory NetworkX.  During retrieval,
if a query returns low-confidence chunks, the pipeline traverses the graph to
fetch prerequisite concepts and prepend them as context.

Install:
    pip install networkx   (usually already installed as a dependency)

Schema:
    SQLite table 'triples': (subject TEXT, relation TEXT, object TEXT)
    NetworkX DiGraph: edge subject -> object means "subject is_prerequisite_for object"
    (relation "is_prerequisite_for" or "requires" creates an edge in this direction)

Usage:
    from knowledge_graph import build_graph, get_prerequisites, extract_and_add

    G = build_graph()                  # load from SQLite
    prereqs = get_prerequisites(G, "Lagrangian mechanics", depth=2)
    extract_and_add(G, chunk_text)     # call during embed
"""
import json
import sqlite3
from pathlib import Path

from config import METADATA

DB_PATH = METADATA / "knowledge_graph.db"

# Relations that indicate concept B is a prerequisite for concept A
# (edge direction: B -> A in the prerequisite graph)
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


def _init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS triples (
            subject  TEXT NOT NULL,
            relation TEXT NOT NULL,
            object   TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (subject, relation, object)
        )
    """)
    # Migrate: add weight column if missing (existing DBs pre-PathRAG)
    try:
        conn.execute("ALTER TABLE triples ADD COLUMN weight INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn


def build_graph(db_path: Path | None = None) -> "networkx.DiGraph":
    """Load all triples from SQLite and return a NetworkX directed graph.

    Nodes are concept strings.  Edges are (prerequisite -> concept) pairs.
    """
    try:
        import networkx as nx
    except ImportError:
        raise ImportError("networkx is required: pip install networkx")

    G = nx.DiGraph()
    db = db_path or DB_PATH
    if not db.exists():
        return G

    conn = _init_db(db)
    for row in conn.execute("SELECT subject, relation, object, weight FROM triples"):
        subj, rel, obj, weight = row
        G.add_edge(subj.lower(), obj.lower(), relation=rel, weight=int(weight or 1))
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
    """PathRAG-style weighted BFS: returns top-k reliable prerequisite paths.

    Score = min_edge_weight * decay**depth — reliability × depth-penalty combined.
    Only edges with weight >= min_weight are traversed, pruning single-occurrence
    noisy relations. Deeper hops are penalised by decay^depth so shallow reliable
    paths win over long uncertain ones.
    """
    try:
        import networkx as nx
    except ImportError:
        return []

    concept_lower = concept.lower()
    if concept_lower not in G:
        return []

    # BFS collecting (node, depth, min_path_weight)
    # ponytail: score = min_path_w * decay**d — reliability × depth-penalty in one number
    scored: dict[str, float] = {}
    queue: list[tuple[str, int, float]] = [(concept_lower, 0, float("inf"))]
    while queue:
        node, d, path_w = queue.pop(0)
        if d >= depth:
            continue
        for pred in G.predecessors(node):
            edge_w = G[pred][node].get("weight", 1)
            if edge_w < min_weight:
                continue
            new_path_w = min(path_w, edge_w)
            score = new_path_w * (decay ** (d + 1))
            if pred not in scored or scored[pred] < score:
                scored[pred] = score
                queue.append((pred, d + 1, new_path_w))

    scored.pop(concept_lower, None)
    # Return top_k by reliability score
    return [c for c, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:top_k]]


def extract_triples(chunk_text: str) -> list[dict]:
    """Call LLM to extract concept-relation triples from a chunk.

    Returns list of {"subject": ..., "relation": ..., "object": ...} dicts.
    """
    try:
        from llm import call_gemini, call_groq
        messages = [
            {"role": "system", "content": _TRIPLE_SYSTEM},
            {"role": "user", "content": chunk_text[:2000]},
        ]
        try:
            raw = call_gemini(messages, model="gemini-2.0-flash", max_tokens=512)
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
    """Persist triples to SQLite, incrementing weight on re-insertion."""
    if not triples:
        return
    conn = _init_db(db_path or DB_PATH)
    conn.executemany(
        """INSERT INTO triples (subject, relation, object, weight) VALUES (?, ?, ?, 1)
           ON CONFLICT(subject, relation, object) DO UPDATE SET weight = weight + 1""",
        [(t["subject"], t["relation"], t["object"]) for t in triples],
    )
    conn.commit()
    conn.close()


def extract_and_add(chunk_text: str, db_path: Path | None = None) -> list[dict]:
    """Extract triples from chunk and persist them. Returns extracted triples."""
    triples = extract_triples(chunk_text)
    add_triples(triples, db_path)
    return triples
