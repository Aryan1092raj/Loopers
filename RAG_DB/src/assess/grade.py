import json
import uuid
import datetime
from core.db import get_connection
from learner.bkt import update_p_know
from learner.fsrs import next_interval
import logging

logger = logging.getLogger(__name__)

def grade_submission(student_id: str, quiz_id: str, item_id: str, student_answer: list) -> bool:
    """
    Evaluates response, persists attempt, logs evidence, and triggers B3 cognitive updates
    in a SINGLE ATOMIC TRANSACTION.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        # 1. Fetch Item
        item = conn.execute("SELECT concept_id, answer_json FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not item:
            raise ValueError(f"Item {item_id} not found in database.")
            
        concept_id, ans_str = item
        correct_ans = json.loads(ans_str)
        
        is_correct = sorted(student_answer) == sorted(correct_ans)
        score = 1.0 if is_correct else 0.0
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # 2. Log Attempt
        attempt_id = f"att_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO attempts (attempt_id, quiz_id, item_id, student_id, response_json, correct, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (attempt_id, quiz_id, item_id, student_id, json.dumps(student_answer), score, now.isoformat())
        )
        
        # 3. Log Evidence
        evidence_id = f"ev_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO evidence (evidence_id, student_id, concept_id, kind, correct, score, raw_data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, student_id, concept_id, "quiz", 1.0 if is_correct else 0.0, score, json.dumps(student_answer), now.isoformat())
        )
        
        # 4. Fetch Learner State inside the transaction
        row = conn.execute(
            "SELECT p_mastery, stability, difficulty, last_reviewed, next_review FROM learner_state WHERE student_id=? AND concept_id=?",
            (student_id, concept_id)
        ).fetchone()
        
        if row:
            p_mastery, stability, difficulty = row[0], row[1], row[2]
            last_reviewed = datetime.datetime.fromisoformat(row[3]) if row[3] else None
            if last_reviewed and last_reviewed.tzinfo is None:
                last_reviewed = last_reviewed.replace(tzinfo=datetime.timezone.utc)
        else:
            p_mastery, stability, difficulty = 0.1, 1.0, 5.0
            last_reviewed = None
            
        # 5. Cognitive Updates (BKT + FSRS)
        new_p = update_p_know(
            p_know=p_mastery, 
            p_slip=0.1, 
            p_guess=0.2, 
            p_trans=0.05, 
            correct=is_correct
        )
        
        rating = 3 if is_correct else 1
        next_rev, new_s, new_d = next_interval(
            rating=rating,
            stability=stability,
            difficulty=difficulty,
            last_review=last_reviewed,
            current_time=now
        )
        
        # 6. Commit State
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
        """, (student_id, concept_id, new_p, new_s, new_d, now.isoformat(), next_rev.isoformat(), now.isoformat()))
        
        conn.commit()
        return is_correct
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Transaction failed during grading/cognitive update: {e}")
        raise e
    finally:
        conn.close()
