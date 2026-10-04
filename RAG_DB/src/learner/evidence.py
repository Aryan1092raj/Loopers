import uuid
import datetime
from core.db import get_connection
from learner.state import get_learner_state, update_learner_state
from learner.bkt import update_p_know
from learner.fsrs import next_interval

def log_evidence(student_id: str, concept_id: str, interaction_type: str, correct: bool, score: float, raw_data: str) -> None:
    """
    Appends interaction to evidence log and atomically updates learner mastery state.
    """
    evidence_id = f"ev_{uuid.uuid4().hex[:8]}"
    now = datetime.datetime.now()
    
    conn = get_connection()
    try:
        # 1. Append-only Evidence Record
        conn.execute(
            "INSERT INTO evidence (evidence_id, student_id, concept_id, kind, correct, score, raw_data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, student_id, concept_id, interaction_type, 1.0 if correct else 0.0, score, raw_data, now.isoformat())
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    # 2. Retrieve current state
    state = get_learner_state(student_id, concept_id)
    
    # 3. Update BKT Mastery (p_know)
    p_know = update_p_know(
        p_know=state["p_mastery"], 
        p_slip=0.1, 
        p_guess=0.2, 
        p_trans=0.05, 
        correct=correct
    )
    
    # 4. Update FSRS (Spacing)
    rating = 3 if correct else 1
    next_rev, new_s, new_d = next_interval(
        rating=rating,
        stability=state["stability"],
        difficulty=state["difficulty"],
        last_review=state["last_reviewed"],
        current_time=now
    )
    
    # 5. Commit updated state
    state["p_mastery"] = p_know
    state["stability"] = new_s
    state["difficulty"] = new_d
    state["last_reviewed"] = now
    state["next_review"] = next_rev
    
    update_learner_state(student_id, concept_id, state)
