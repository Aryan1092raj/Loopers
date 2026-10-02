import json
import logging
from typing import List

from core.models import SourceUnit
from core.db import get_connection

# Conditional imports to handle testing/environments where Qdrant/BGE-M3 aren't fully initialized
try:
    import store
    import embeddings
except ImportError as e:
    logging.getLogger(__name__).warning(f"Vector dependencies missing, Qdrant upserts will fail: {e}")

logger = logging.getLogger(__name__)

def write_units(units: List[SourceUnit]) -> None:
    """
    Persists SourceUnit records to the SQLite relational database and the Qdrant vector store.
    Transactional safety is prioritized for relational data.
    """
    if not units:
        return

    # 1. Write to SQLite (Transactional)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for unit in units:
            # Map Pydantic locator to relational columns
            page_no = unit.locator.page_no if unit.locator else None
            slide_no = unit.locator.slide_no if unit.locator else None
            t_start = unit.locator.t_start if unit.locator else None
            t_end = unit.locator.t_end if unit.locator else None
            matched_slide = unit.locator.matched_slide_page if unit.locator else None
            bbox_json = json.dumps(unit.locator.bbox) if unit.locator and unit.locator.bbox else None
            
            cursor.execute(
                """
                INSERT INTO units (
                    unit_id, source_id, kind, heading, text, page_no, slide_no, 
                    t_start, t_end, matched_slide_page, bbox_json, image_path, 
                    latex_json, lang, topic_id, has_math
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id) DO UPDATE SET
                    heading=excluded.heading,
                    text=excluded.text,
                    page_no=excluded.page_no,
                    slide_no=excluded.slide_no,
                    t_start=excluded.t_start,
                    t_end=excluded.t_end,
                    matched_slide_page=excluded.matched_slide_page,
                    bbox_json=excluded.bbox_json,
                    image_path=excluded.image_path,
                    latex_json=excluded.latex_json,
                    lang=excluded.lang,
                    topic_id=excluded.topic_id,
                    has_math=excluded.has_math
                """,
                (
                    unit.unit_id,
                    unit.source_id,
                    unit.kind,
                    unit.heading,
                    unit.text,
                    page_no,
                    slide_no,
                    t_start,
                    t_end,
                    matched_slide,
                    bbox_json,
                    unit.image_path,
                    json.dumps(unit.latex),
                    unit.lang,
                    unit.topic_id,
                    1 if unit.has_math else 0
                )
            )
            
            # Map associated concepts
            for concept_id in unit.concept_ids:
                cursor.execute(
                    """
                    INSERT INTO unit_concepts (unit_id, concept_id, score)
                    VALUES (?, ?, ?)
                    ON CONFLICT(unit_id, concept_id) DO NOTHING
                    """,
                    (unit.unit_id, concept_id, 1.0)
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"SQLite insertion failed for units: {e}")
        raise
    finally:
        conn.close()

    # 2. Write to Qdrant
    try:
        texts = [u.text for u in units]
        dense_vecs, sparse_vecs = embeddings.encode_hybrid(texts)
        
        ids = [u.unit_id for u in units]
        payloads = [
            {
                "unit_id": u.unit_id,
                "source_id": u.source_id,
                "source_type": u.source_type,
                "kind": u.kind,
                "title": u.title,
                "heading": u.heading,
                "text": u.text,
                "parent_text": u.parent_text,
                "page_no": u.locator.page_no if u.locator else None,
                "slide_no": u.locator.slide_no if u.locator else None,
                "t_start": u.locator.t_start if u.locator else None,
                "t_end": u.locator.t_end if u.locator else None,
                "matched_slide_page": u.locator.matched_slide_page if u.locator else None,
                "image": u.image_path,
                "latex": u.latex,
                "lang": u.lang,
                "topic_id": u.topic_id,
                "concept_ids": u.concept_ids,
                "has_math": u.has_math
            } for u in units
        ]
        
        q_client = store.client()
        store.ensure_indexes(q_client)
        store.upsert(q_client, ids, dense_vecs, payloads, sparse_vecs)
    except Exception as e:
        logger.error(f"Qdrant insertion failed for units: {e}")
        raise
