"""Build the concept graph and tag every unit (A3). Run it with `python src/pipeline.py concepts`.

units -> windows -> LLM concept mentions -> clusters -> topics -> prerequisite edges -> DAG -> unit tags.
It rewrites `concepts`, `concept_edges` and `unit_concepts` in full, so it can run again after new
sources arrive (LLM answers for unchanged windows come from the cache).
"""
import json

from concepts import cluster, dag, extract, hierarchy, prereq, tag
from config import COLLECTION, METADATA
from core.db import get_connection, init_db

REPORT = METADATA / "concept_report.json"


def _llm():
    from tutor.answer import complete

    return lambda prompt: complete([{"role": "user", "content": prompt}], role="concepts", max_tokens=2000)


def _encode():
    from embeddings import encode_hybrid

    return lambda texts: encode_hybrid(texts)[0]


def _units() -> list[dict]:
    """All units in reading order: lecture, then source, then page, slide or time."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT u.unit_id, u.text, s.title, s.lecture_no FROM units u LEFT JOIN sources s ON s.source_id = u.source_id"
            " ORDER BY COALESCE(s.lecture_no, 0), s.created_at, u.source_id,"
            " COALESCE(u.page_no, u.slide_no, u.t_start, 0), u.rowid"
        ).fetchall()
    finally:
        conn.close()
    return [{"unit_id": r[0], "text": r[1], "title": r[2], "lecture_no": r[3]} for r in rows]


def _save(units, mentions, owner, topics, concepts, kept, why, tags) -> dict[str, str | None]:
    lecture = {u["unit_id"]: u["lecture_no"] for u in units}
    first_lecture = {}
    for m, c in zip(mentions, owner):
        if m["pos"] == concepts[c]["first_pos"]:
            first_lecture.setdefault(concepts[c]["id"], lecture[m["unit_id"]])
    parent = {c["id"]: c["parent_id"] for c in concepts}
    topic_of = {uid: parent[picks[0][0]] for uid, picks in tags.items()}
    conn = get_connection()
    try:
        # children before parents; fails loudly if a student's mastery still points at a concept
        for table in ("unit_concepts", "concept_edges", "concepts"):
            conn.execute(f"DELETE FROM {table}")
        for node in [*topics, *concepts]:  # topics first: parent_id is a foreign key
            conn.execute(
                "INSERT INTO concepts (concept_id, name, level, parent_id, aliases_json, description, first_seen_lecture)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (node["id"], node["name"], node["level"], node["parent_id"], json.dumps(node["aliases"]),
                 node["definition"], first_lecture.get(node["id"])),
            )
        conn.executemany(
            "INSERT INTO concept_edges (src, dst, weight, evidence) VALUES (?, ?, ?, ?)",
            [(s, d, w, why.get((s, d))) for s, d, w in kept],
        )
        for uid, picks in tags.items():
            conn.executemany("INSERT INTO unit_concepts (unit_id, concept_id, score) VALUES (?, ?, ?)", [(uid, c, s) for c, s in picks])
            conn.execute("UPDATE units SET topic_id = ? WHERE unit_id = ?", (topic_of[uid], uid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return topic_of


def _tag_qdrant(tags, topic_of) -> None:
    """Copy tags into the Qdrant payload so retrieval can filter by concept and topic."""
    import store

    try:
        client = store.client()
        store.ensure_indexes(client)
        for uid, picks in tags.items():
            client.set_payload(COLLECTION, payload={"concept_ids": [c for c, _ in picks], "topic_id": topic_of[uid]},
                               points=[store._point_id(uid)])
    except Exception as e:  # tags are in SQLite already; a Qdrant outage must not undo them
        print(f"[concepts] could not update Qdrant payloads: {e}")


def run(llm=None, encode=None, qdrant: bool = True) -> dict:
    init_db()
    units = _units()
    if not units:
        print("[concepts] no units in the database — run embed first")
        return {}
    llm, encode = llm or _llm(), encode or _encode()
    mentions = extract.extract(units, llm)
    if not mentions:
        raise RuntimeError("the LLM extracted no concepts from any unit")
    concepts, owner = cluster.cluster_mentions(mentions, encode)
    topics = hierarchy.build_hierarchy(concepts, encode, llm, course=units[0]["title"] or "Course")
    edges = prereq.score_edges(concepts, mentions, owner, llm)
    kept, removed = dag.make_acyclic([(s, d, w) for s, d, w, _ in edges])
    tags, weak = tag.tag_units(units, encode([u["text"] for u in units]), concepts, llm)
    topic_of = _save(units, mentions, owner, topics, concepts, kept, {(s, d): w for s, d, _, w in edges}, tags)
    if qdrant:
        _tag_qdrant(tags, topic_of)
    report = {
        "units": len(units), "topics": len(topics), "concepts": len(concepts), "edges": len(kept),
        "removed_cycle_edges": [{"src": s, "dst": d, "weight": w} for s, d, w in removed], "weak_tags": weak,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    print(f"[concepts] {len(concepts)} concepts under {len(topics)} topics, {len(kept)} prerequisite edges "
          f"({len(removed)} removed to break cycles), {len(units)} units tagged ({len(weak)} weakly)")
    return report
