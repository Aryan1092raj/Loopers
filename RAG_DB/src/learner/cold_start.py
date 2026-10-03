import json
import uuid
import datetime
from typing import Tuple, Dict, Any
from core.db import get_connection
from learner.irt import update_ability
from learner.evidence import log_evidence
from learner.state import get_learner_state, update_learner_state

def get_priors(background: str) -> Tuple[float, float, float]:
    """
    Maps intake background string to cognitive priors:
    Returns (theta_0, p_mastery_0, stability_0)
    """
    bg = background.lower()
    if bg == "advanced":
        return 1.0, 0.6, 3.0
    elif bg == "intermediate":
        return 0.0, 0.4, 2.0
    return -1.0, 0.2, 1.0

def onboard_student(student_id: str, background: str) -> float:
    """
    Registers a student and initializes their base IRT theta in the intake profile.
    Priors will be lazily flushed to concepts during diagnostic testing or evidence generation.
    """
    theta_0, _, _ = get_priors(background)
    conn = get_connection()
    now = datetime.datetime.now().isoformat()
    
    intake_json = json.dumps({
        "background": background,
        "theta": theta_0,
        "onboarded_at": now
    })
    
    conn.execute("""
        INSERT INTO students (student_id, name, intake_json, created_at) 
        VALUES (?, ?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET intake_json=excluded.intake_json
    """, (student_id, "New Learner", intake_json, now))
    
    conn.commit()
    conn.close()
    return theta_0

def get_next_diagnostic_item(student_id: str) -> Dict[str, Any] | None:
    """
    Adaptive router: Selects the next diagnostic question maximizing Fisher Information
    (chooses the unanswered item with difficulty closest to the student's current ability theta).
    """
    conn = get_connection()
    row = conn.execute("SELECT intake_json FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Student {student_id} not found.")
        
    intake = json.loads(row[0] or "{}")
    theta = intake.get("theta", 0.0)
    
    # Adaptive Selection: absolute difference between item difficulty (b) and student ability (theta)
    query = """
        SELECT item_id, concept_id, difficulty_b, stem, options_json 
        FROM items 
        WHERE item_id NOT IN (SELECT item_id FROM attempts WHERE student_id=?)
        ORDER BY abs(difficulty_b - ?) ASC LIMIT 1
    """
    item = conn.execute(query, (student_id, theta)).fetchone()
    conn.close()
    
    if not item:
        return None
        
    return {
        "item_id": item[0],
        "concept_id": item[1],
        "difficulty": item[2],
        "stem": item[3],
        "options_json": item[4]
    }

def process_diagnostic_response(student_id: str, item_id: str, correct: bool) -> float:
    """
    Processes a diagnostic answer:
    1. Updates latent theta via IRT.
    2. Initializes B3 learner_state priors (Cold Start resolution).
    3. Commits evidence log (which intrinsically mutates BKT and FSRS state).
    """
    conn = get_connection()
    item_row = conn.execute("SELECT concept_id, difficulty_b FROM items WHERE item_id=?", (item_id,)).fetchone()
    if not item_row:
        conn.close()
        raise ValueError(f"Item {item_id} not found.")
        
    concept_id, b = item_row
    
    stu_row = conn.execute("SELECT intake_json FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not stu_row:
        conn.close()
        raise ValueError(f"Student {student_id} not found.")
        
    intake = json.loads(stu_row[0] or "{}")
    theta = intake.get("theta", 0.0)
    bg = intake.get("background", "beginner")
    
    # 1. Update Theta (IRT)
    new_theta = update_ability(theta, b, correct, lr=0.5)
    intake["theta"] = new_theta
    
    # Record attempt locally to mark it answered
    attempt_id = f"att_{uuid.uuid4().hex[:8]}"
    conn.execute("""
        INSERT INTO attempts (attempt_id, quiz_id, item_id, student_id, response_json, correct, ts) 
        VALUES (?, 'diagnostic', ?, ?, '{}', ?, datetime('now'))
    """, (attempt_id, item_id, student_id, 1.0 if correct else 0.0))
    
    conn.execute("UPDATE students SET intake_json=? WHERE student_id=?", (json.dumps(intake), student_id))
    conn.commit()
    conn.close()
    
    # 2. Resolve Cold Start Priors for this concept if uninitialized
    state = get_learner_state(student_id, concept_id)
    if state["last_reviewed"] is None:
        _, p_prior, s_prior = get_priors(bg)
        state["p_mastery"] = p_prior
        state["stability"] = s_prior
        update_learner_state(student_id, concept_id, state)
        
    # 3. Log Evidence (Delegates to BKT and FSRS for actual updates)
    log_evidence(
        student_id=student_id,
        concept_id=concept_id,
        interaction_type="diagnostic",
        correct=correct,
        score=1.0 if correct else 0.0,
        raw_data="{}"
    )
    
    return new_theta

