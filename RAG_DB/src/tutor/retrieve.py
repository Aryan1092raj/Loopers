"""Tutor retrieval: `generate.retrieve` wrapped to return `Evidence[]` (TRD §3.1).

Differences from the notes and deck path: RRF order is kept (no has_math re-sort), and prerequisite
evidence comes from the concepts tagged on the question, not from the last word of the query.
"""
from concepts import api as concepts
from core.db import get_connection
from core.models import Evidence, Locator, SourceUnit

MAX_PREREQ_CONCEPTS = 2   # tagged concepts whose prerequisites are fetched
PREREQ_PER_CONCEPT = 1    # prerequisite concepts looked up per tagged concept


def to_unit(p: dict) -> SourceUnit:
    """A Qdrant payload as a SourceUnit. Locators are copied from stored fields, never inferred."""
    kind = p.get("kind") or "text"
    return SourceUnit(
        unit_id=str(p.get("unit_id") or p.get("chunk_id") or p.get("id") or ""),
        source_id=p.get("source_id") or p.get("doc") or "",
        source_type=p.get("source_type") or ("video" if p.get("t_start") is not None else "textbook"),
        kind="figure" if kind == "image" else kind,
        title=p.get("title") or p.get("doc") or "",
        heading=p.get("heading"),
        text=p.get("text") or "",
        parent_text=p.get("parent_text"),
        locator=Locator(
            page_no=p.get("page_no"), slide_no=p.get("slide_no"), t_start=p.get("t_start"), t_end=p.get("t_end"),
            bbox=p.get("bbox"), matched_slide_page=p.get("matched_slide_page"),
        ),
        image_path=p.get("image"),
        latex=p.get("latex") or [],
        lang=p.get("lang") or "en",
        topic_id=p.get("topic_id"),
        concept_ids=p.get("concept_ids") or [],
        has_math=bool(p.get("has_math")),
    )


def _prerequisite_chunks(query: str) -> list[dict]:
    """One chunk about each prerequisite of the concepts tagged on the query."""
    import store
    from embeddings import encode_hybrid

    out = []
    for cid in concepts.tag_for_text(query)[:MAX_PREREQ_CONCEPTS]:
        for pid in concepts.prerequisites(cid)[:PREREQ_PER_CONCEPT]:
            node = concepts.get_concept(pid)
            if node is None:
                continue
            dense, sparse = encode_hybrid(node["name"])
            for chunk in store.query(store.client(), dense[0], k=1, sparse_vector=sparse[0] if sparse else None,
                                     kind="text", math_boost=False):
                chunk["_is_prerequisite"] = True
                out.append(chunk)
    return out


def _in_scope(unit_ids: list[str], scope: dict) -> set[str]:
    """Unit ids tagged with a concept inside the scope."""
    allowed = concepts.concepts_for_scope(scope)
    if not unit_ids or not allowed:
        return set()
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT unit_id FROM unit_concepts WHERE concept_id IN ({','.join('?' * len(allowed))})"
            f" AND unit_id IN ({','.join('?' * len(unit_ids))})",
            [*allowed, *unit_ids],
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _units_tagged() -> bool:
    """False until the concept build has tagged at least one unit."""
    conn = get_connection()
    try:
        return conn.execute("SELECT 1 FROM unit_concepts LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def retrieve_evidence(query: str, scope: dict | None = None) -> tuple[list[Evidence], bool]:
    """Evidence E1..En for a question, and whether the cross-encoder reranked it (False means degraded scores)."""
    from generate import retrieve  # heavy: loads BGE-M3, reranker and Qdrant on first call

    chunks, reranked = retrieve(query, tutor=True)
    # an untagged corpus would filter every chunk out, so the scope applies only once the concept build has run
    if scope and (scope.get("topic_ids") or scope.get("concept_ids")) and _units_tagged():
        keep = _in_scope([str(c.get("unit_id") or "") for c in chunks], scope)
        chunks = [c for c in chunks if str(c.get("unit_id") or "") in keep]
    if chunks:  # background for the question; nothing to add when the question matched nothing
        try:
            chunks = chunks + _prerequisite_chunks(query)
        except Exception as e:  # prerequisites are optional context, never a reason to fail the answer
            print(f"[tutor] prerequisite lookup failed: {e}")
    # a paged source whose chunks carry no locator (stale extraction) cannot be cited, so it is never evidence
    chunks = [c for c in chunks if c.get("doc_type") != "pdf"
              or any(c.get(k) is not None for k in ("page_no", "slide_no", "t_start"))]
    evidence = [
        Evidence(id=f"E{i}", unit=to_unit(c), score=float(c.get("score", 0.0)), is_prerequisite=bool(c.get("_is_prerequisite")))
        for i, c in enumerate(chunks, 1)
    ]
    return evidence, reranked
