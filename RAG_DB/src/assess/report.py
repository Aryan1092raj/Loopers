from core.db import get_connection

def generate_report(student_id: str, quiz_id: str) -> dict:
    """
    Compiles diagnostic telemetry into a review action plan.
    """
    conn = get_connection()
    attempts = conn.execute(
        "SELECT item_id, correct FROM attempts WHERE quiz_id=? AND student_id=?", 
        (quiz_id, student_id)
    ).fetchall()
    
    if not attempts:
        conn.close()
        return {"status": "error", "message": "No attempts found."}
        
    total = len(attempts)
    correct = sum(a[1] for a in attempts)
    
    # Identify active concepts tested
    concepts = conn.execute(
        """SELECT DISTINCT i.concept_id 
           FROM attempts a 
           JOIN items i ON a.item_id = i.item_id 
           WHERE a.quiz_id=? AND a.student_id=?""",
        (quiz_id, student_id)
    ).fetchall()
    
    weak_concepts = []
    for (cid,) in concepts:
        state = conn.execute("SELECT p_mastery FROM learner_state WHERE student_id=? AND concept_id=?", (student_id, cid)).fetchone()
        if state and state[0] < 0.6:
            weak_concepts.append(cid)
            
    conn.close()
    
    return {
        "quiz_id": quiz_id,
        "student_id": student_id,
        "score_ratio": correct / total,
        "total_items": total,
        "correct_items": correct,
        "remediation_recommended": weak_concepts
    }
