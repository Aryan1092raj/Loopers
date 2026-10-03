import json
import uuid
from core.db import get_connection
from learner.evidence import log_evidence
import logging

logger = logging.getLogger(__name__)

def grade_submission(student_id: str, quiz_id: str, item_id: str, student_answer: list) -> bool:
    """
    Evaluates response, persists attempt, and triggers B3 cognitive updates.
    """
    conn = get_connection()
    item = conn.execute("SELECT concept_id, answer_json FROM items WHERE item_id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        raise ValueError(f"Item {item_id} not found in database.")
        
    concept_id, ans_str = item
    correct_ans = json.loads(ans_str)
    
    is_correct = sorted(student_answer) == sorted(correct_ans)
    score = 1.0 if is_correct else 0.0
    
    attempt_id = f"att_{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO attempts (attempt_id, quiz_id, item_id, student_id, response_json, correct, ts) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (attempt_id, quiz_id, item_id, student_id, json.dumps(student_answer), score)
    )
    conn.commit()
    conn.close()
    
    # Delegate heavily to B3 architecture for evidence logging and mastery mutability
    log_evidence(
        student_id=student_id, 
        concept_id=concept_id, 
        interaction_type="quiz", 
        correct=is_correct, 
        score=score, 
        raw_data=json.dumps(student_answer)
    )
    
    return is_correct
