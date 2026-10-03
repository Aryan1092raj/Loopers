import datetime
from core.db import get_connection

def get_learner_state(student_id: str, concept_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT p_mastery, stability, difficulty, last_reviewed, next_review FROM learner_state WHERE student_id=? AND concept_id=?",
        (student_id, concept_id)
    ).fetchone()
    conn.close()
    
    if row:
        return {
            "p_mastery": row[0],
            "stability": row[1],
            "difficulty": row[2],
            "last_reviewed": datetime.datetime.fromisoformat(row[3]) if row[3] else None,
            "next_review": datetime.datetime.fromisoformat(row[4]) if row[4] else None
        }
    return {
        "p_mastery": 0.1, # default prior
        "stability": 1.0, # default S
        "difficulty": 5.0, # default D
        "last_reviewed": None,
        "next_review": None
    }

def update_learner_state(student_id: str, concept_id: str, state: dict) -> None:
    conn = get_connection()
    now = datetime.datetime.now().isoformat()
    last_rev = state["last_reviewed"].isoformat() if state["last_reviewed"] else None
    next_rev = state["next_review"].isoformat() if state["next_review"] else None
    
    conn.execute("""
        INSERT INTO learner_state (student_id, concept_id, p_mastery, stability, difficulty, last_reviewed, next_review, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, concept_id) DO UPDATE SET
            p_mastery=excluded.p_mastery,
            stability=excluded.stability,
            difficulty=excluded.difficulty,
            last_reviewed=excluded.last_reviewed,
            next_review=excluded.next_review,
            updated_at=excluded.updated_at
    """, (student_id, concept_id, state["p_mastery"], state["stability"], state["difficulty"], last_rev, next_rev, now))
    conn.commit()
    conn.close()
