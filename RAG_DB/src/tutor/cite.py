"""Citation resolution. Locators always come from stored fields, never from model output.

Phase 1: builds `Citation` objects from stored payloads and `units` rows, without
media URLs. A1 adds page images and A5 adds marker validation (TRD §7.4).
"""
import json

from core.db import get_connection
from core.models import Citation, Locator

EXCERPT_CHARS = 300


def citation_from_payload(eid: str, p: dict) -> Citation:
    """Map a Qdrant payload (or a `units` row shaped like one) to a Citation."""
    bbox = p.get("bbox")
    if isinstance(bbox, str):
        bbox = json.loads(bbox)
    return Citation(
        id=eid,
        unit_id=str(p.get("unit_id") or p.get("chunk_id") or p.get("id") or ""),
        source_id=p.get("source_id") or p.get("doc") or "",
        source_type=p.get("source_type") or ("video" if p.get("t_start") is not None else "textbook"),
        title=p.get("title") or p.get("doc") or "",
        locator=Locator(
            page_no=p.get("page_no"),
            slide_no=p.get("slide_no"),
            t_start=p.get("t_start"),
            t_end=p.get("t_end"),
            bbox=bbox,
            matched_slide_page=p.get("matched_slide_page"),
        ),
        excerpt=(p.get("text") or "")[:EXCERPT_CHARS],
    )


def resolve(unit_ids: list[str]) -> list[Citation]:
    """Citations for unit ids from the `units` table, in input order, ids E1..En. Unknown ids are skipped."""
    if not unit_ids:
        return []
    conn = get_connection()
    conn.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
    try:
        marks = ",".join("?" * len(unit_ids))
        rows = conn.execute(
            f"SELECT u.unit_id, u.source_id, u.text, u.page_no, u.slide_no, u.t_start, u.t_end,"
            f" u.matched_slide_page, u.bbox_json AS bbox, s.title, s.kind AS source_type"
            f" FROM units u LEFT JOIN sources s ON s.source_id = u.source_id WHERE u.unit_id IN ({marks})",
            unit_ids,
        ).fetchall()
    finally:
        conn.close()
    by_id = {r["unit_id"]: r for r in rows}
    found = [by_id[u] for u in unit_ids if u in by_id]
    return [citation_from_payload(f"E{i}", r) for i, r in enumerate(found, 1)]
