from rapidfuzz import fuzz
from core.db import get_connection
import logging

logger = logging.getLogger(__name__)

def check_duplicate(concept_id: str, stem: str, threshold: float = 85.0) -> bool:
    """
    Semantic deduplication against existing items in SQLite.
    Returns True if a duplicate is found (stem similarity exceeds threshold).
    """
    conn = get_connection()
    existing = conn.execute("SELECT stem FROM items WHERE concept_id=?", (concept_id,)).fetchall()
    conn.close()
    
    for (existing_stem,) in existing:
        score = fuzz.token_set_ratio(stem, existing_stem)
        if score > threshold:
            logger.warning(f"Duplicate detected: Score {score:.2f} > {threshold}")
            return True
            
    return False
