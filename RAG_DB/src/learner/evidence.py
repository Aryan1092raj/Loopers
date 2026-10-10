import uuid
import datetime
from core.db import get_connection
from core.models import MasteryUpdate
from learner.state import get_learner_state, update_learner_state
from learner.bkt import update_p_know
from learner.fsrs import next_interval

CHAT_P_TRANS = 0.02  # ponytail: fixed small learn step per chat turn, calibrate later

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


def observe(student_id: str, message: str, evidence: list, max_concepts: int = 3) -> list[MasteryUpdate]:
    """
    Chat turn = exposure, not a graded answer: apply only the BKT learn transition to the
    concepts of the top retrieved units (no slip/guess observation, FSRS untouched), and
    record each change in mastery_events.
    """
    unit_ids = [e.unit.unit_id for e in evidence[:3]]
    if not unit_ids:
        return []
    conn = get_connection()
    try:
        marks = ",".join("?" * len(unit_ids))
        rows = conn.execute(
            f"SELECT concept_id, MAX(score) s FROM unit_concepts WHERE unit_id IN ({marks})"
            " GROUP BY concept_id ORDER BY s DESC LIMIT ?", (*unit_ids, max_concepts)
        ).fetchall()
        now = datetime.datetime.now()
        updates = []
        for concept_id, _ in rows:
            state = get_learner_state(student_id, concept_id)
            before = state["p_mastery"]
            after = min(1.0 - 1e-4, before + (1.0 - before) * CHAT_P_TRANS)
            state["p_mastery"] = after
            update_learner_state(student_id, concept_id, state)
            updates.append(MasteryUpdate(concept_id=concept_id, p_before=before, p_after=after))
        # events written after the loop: update_learner_state opens its own connection and would hit our write lock
        conn.executemany(
            "INSERT INTO mastery_events (student_id, concept_id, ts, source, ref_id, y, p_before, p_after)"
            " VALUES (?, ?, ?, 'chat', NULL, NULL, ?, ?)",
            [(student_id, u.concept_id, now.isoformat(), u.p_before, u.p_after) for u in updates],
        )
        conn.commit()
        return updates
    finally:
        conn.close()
