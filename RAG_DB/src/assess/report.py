import json

from core.db import get_connection
from tutor.cite import resolve

WEAK_BELOW = 0.6


def generate_report(student_id: str, quiz_id: str) -> dict:
    """Post-assessment report built from stored attempts and mastery_events; nothing here is estimated."""
    conn = get_connection()
    try:
        attempts = conn.execute(
            "SELECT a.correct, a.misconception, i.concept_id, i.topic_id, i.unit_ids_json FROM attempts a"
            " JOIN items i ON a.item_id=i.item_id WHERE a.quiz_id=? AND a.student_id=?", (quiz_id, student_id)
        ).fetchall()
        if not attempts:
            return {"quiz_id": quiz_id, "score": 0.0, "mastery_delta": [], "weak_topics": [], "misconceptions": [], "next_steps": [], "raw_metrics": {}}

        total = len(attempts)
        correct = sum(a[0] for a in attempts)

        mastery_delta, next_steps, weak = [], [], {}
        for cid in dict.fromkeys(a[2] for a in attempts):
            events = conn.execute(
                "SELECT p_before, p_after FROM mastery_events WHERE student_id=? AND concept_id=? AND source='quiz' AND ref_id=?"
                " ORDER BY event_id", (student_id, cid, quiz_id)
            ).fetchall()
            if not events:  # attempts graded before mastery_events existed
                continue
            before, after = events[0][0], events[-1][1]
            mastery_delta.append({"concept_id": cid, "before": round(before, 3), "after": round(after, 3)})
            if after < WEAK_BELOW:
                next_steps.append({"concept_id": cid, "reason": f"mastery {after:.2f} is below {WEAK_BELOW}"})
                topic = next(a[3] for a in attempts if a[2] == cid)
                weak[topic] = min(weak.get(topic, 1.0), round(after, 2))

        misconceptions, seen = [], set()
        for _, note, cid, _, unit_ids in attempts:
            if note and (cid, note) not in seen:
                seen.add((cid, note))
                m = {"label": note, "concept_id": cid}
                cites = resolve(json.loads(unit_ids))
                if cites:
                    m["citation"] = cites[0].model_dump()
                misconceptions.append(m)
    finally:
        conn.close()

    return {
        "quiz_id": quiz_id,
        "score": correct / total,
        "mastery_delta": mastery_delta,
        "weak_topics": [{"topic_id": t, "p_know": p} for t, p in weak.items()],
        "misconceptions": misconceptions,
        "next_steps": next_steps,
        "raw_metrics": {"total_items": total, "correct_items": int(correct)},
    }
