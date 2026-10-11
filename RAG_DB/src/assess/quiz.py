"""Practice quizzes and mock exams over a student-chosen scope (TRD §6.5).

Every item is grounded in tagged source units, checked for near-duplicates and solved blind by a second model before
it is served. Bank items the student has already been served are never reused, so repeats across assessments only
happen when the bank is exhausted and generation fails.
"""
import json
import uuid
from datetime import datetime, timezone

from assess.dedup import check_duplicate
from assess.generate import NoMaterial, generate_item
from assess.grade import grade_submission, item_feedback
from assess.verify import blind_verify
from core.db import get_connection
from tutor.cite import resolve

DEFAULT_P_KNOW = 0.15
GEN_TRIES = 2          # generation attempts per slot and concept before moving to the next concept
SECONDS_PER_ITEM = 90  # mock exam time budget
PUBLIC_FIELDS = ("item_id", "concept_id", "topic_id", "type", "bloom", "stem", "options_json", "difficulty_b")


def difficulty_label(b: float) -> str:
    return "easy" if b < -0.25 else "medium" if b < 0.25 else "hard"


def public_item(item: dict) -> dict:
    """What the student may see: no key, no rubric. Source locations are tags, not the excerpt text."""
    out = {k: item[k] for k in PUBLIC_FIELDS}
    out["difficulty"] = difficulty_label(item["difficulty_b"])
    out["sources"] = [{"unit_id": c.unit_id, "source_id": c.source_id, "source_type": c.source_type, "title": c.title,
                       "locator": c.locator.model_dump(exclude_none=True)}
                      for c in resolve(json.loads(item["unit_ids_json"]))]
    return out


def store_item(item: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO items (item_id, concept_id, topic_id, type, bloom, stem, options_json, answer_json, rubric_json,"
            " unit_ids_json, difficulty_b, discrimination_a, verified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,datetime('now'))",
            tuple(item[k] for k in ("item_id", "concept_id", "topic_id", "type", "bloom", "stem", "options_json",
                                    "answer_json", "rubric_json", "unit_ids_json", "difficulty_b", "discrimination_a")))
        conn.commit()
    finally:
        conn.close()


def make_item(concept_id: str, topic_id: str, item_type: str = "mcq", stem: str | None = None,
              difficulty: float | None = None) -> tuple[dict | None, str]:
    """Generate, dedup, verify and store one item. Returns (item, "ok") or (None, reason).
    Raises NoMaterial and NoProvider; ValueError/KeyError/JSONDecodeError mean a bad generation."""
    item = generate_item(concept_id, topic_id, item_type, stem, difficulty)
    if check_duplicate(concept_id, item["stem"]):
        return None, "duplicate"
    tolerance = json.loads(item["rubric_json"]).get("tolerance")
    if not blind_verify(item["stem"], item["options_json"], item["answer_json"], item["type"], tolerance):
        return None, "solver_conflict"
    store_item(item)
    return item, "ok"


def _topic_of(conn, concept_id: str) -> str:
    cid = concept_id
    for _ in range(10):
        row = conn.execute("SELECT level, parent_id FROM concepts WHERE concept_id=?", (cid,)).fetchone()
        if not row or row[0] == "topic" or not row[1]:
            return cid
        cid = row[1]
    return cid


def scope_concepts(student_id: str, topic_ids: list[str], concept_ids: list[str]) -> list[tuple[str, str]]:
    """(concept_id, topic_id) of every concept in scope that has tagged units, weakest mastery first.
    An empty scope means the whole course."""
    roots = list(topic_ids) + list(concept_ids)
    conn = get_connection()
    try:
        if roots:
            marks = ",".join("?" * len(roots))
            ids = [r[0] for r in conn.execute(
                f"WITH RECURSIVE sub(id) AS (SELECT concept_id FROM concepts WHERE concept_id IN ({marks})"
                " UNION SELECT c.concept_id FROM concepts c JOIN sub ON c.parent_id=sub.id)"
                " SELECT DISTINCT uc.concept_id FROM unit_concepts uc WHERE uc.concept_id IN (SELECT id FROM sub)", roots)]
        else:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT concept_id FROM unit_concepts")]
        mastery = dict(conn.execute("SELECT concept_id, p_mastery FROM learner_state WHERE student_id=?", (student_id,)).fetchall())
        pairs = [(c, _topic_of(conn, c)) for c in ids]
    finally:
        conn.close()
    return sorted(pairs, key=lambda p: (mastery.get(p[0], DEFAULT_P_KNOW), p[0]))


def _bank_item(conn, student_id: str, concept_id: str, item_type: str, difficulty: float | None, taken: set) -> dict | None:
    conn.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
    rows = conn.execute(
        "SELECT * FROM items WHERE concept_id=? AND type=? AND verified=1"
        " AND item_id NOT IN (SELECT item_id FROM served_items WHERE student_id=?) ORDER BY created_at",
        (concept_id, item_type, student_id)).fetchall()
    conn.row_factory = None
    for r in rows:
        if r["item_id"] not in taken and (difficulty is None or abs(r["difficulty_b"] - difficulty) <= 0.5):
            return r
    return None


def build_quiz(student_id: str, mode: str, topic_ids: list[str], concept_ids: list[str], mix: dict[str, int],
               difficulty: float | None = None) -> dict:
    """Fill each slot in `mix` from the unserved bank first, else by generating a new verified item.
    Slots rotate over the in-scope concepts, weakest first, so a mock exam spreads over the scope."""
    concepts = scope_concepts(student_id, topic_ids, concept_ids)
    if not concepts:
        raise NoMaterial("no concept in scope has tagged source material")
    slots = [t for t, n in mix.items() for _ in range(max(0, n))]
    items, taken, skipped, k = [], set(), [], 0
    for item_type in slots:
        found = None
        for _ in range(len(concepts)):
            concept_id, topic_id = concepts[k % len(concepts)]
            k += 1
            conn = get_connection()
            try:
                found = _bank_item(conn, student_id, concept_id, item_type, difficulty, taken)
            finally:
                conn.close()
            for _ in range(0 if found else GEN_TRIES):
                try:
                    found, _reason = make_item(concept_id, topic_id, item_type, difficulty=difficulty)
                except NoMaterial:
                    break
                except (ValueError, KeyError, json.JSONDecodeError):
                    found = None
                if found:
                    break
            if found:
                break
        if found:
            items.append(found)
            taken.add(found["item_id"])
        else:
            skipped.append(item_type)

    quiz_id = f"quiz_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO quizzes (quiz_id, student_id, mode, scope_json, created_at) VALUES (?,?,?,?,?)",
                     (quiz_id, student_id, mode, json.dumps({"topic_ids": topic_ids, "concept_ids": concept_ids,
                                                             "mix": mix, "difficulty": difficulty}), now))
        for pos, it in enumerate(items):
            conn.execute("INSERT INTO quiz_items (quiz_id, item_id, position) VALUES (?,?,?)", (quiz_id, it["item_id"], pos))
            conn.execute("INSERT OR IGNORE INTO served_items (student_id, item_id, ts) VALUES (?,?,?)", (student_id, it["item_id"], now))
        conn.commit()
    finally:
        conn.close()
    out = {"quiz_id": quiz_id, "mode": mode, "items": [public_item(i) for i in items], "skipped": skipped}
    if mode == "mock":
        out["time_limit_s"] = SECONDS_PER_ITEM * len(items)
    return out


def submit_answers(quiz_id: str, answers: list[dict]) -> dict:
    """Grade each answer (BKT and FSRS update per item) and return cited feedback plus the mastery updates."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT student_id FROM quizzes WHERE quiz_id=?", (quiz_id,)).fetchone()
        if not row:
            raise ValueError(f"quiz {quiz_id} not found")
        student_id = row[0]
        allowed = {r[0] for r in conn.execute("SELECT item_id FROM quiz_items WHERE quiz_id=?", (quiz_id,))}
    finally:
        conn.close()
    results = []
    for a in answers:
        if a["item_id"] not in allowed:
            raise ValueError(f"item {a['item_id']} is not in quiz {quiz_id}")
        correct = grade_submission(student_id, quiz_id, a["item_id"], [a["response"]])
        conn = get_connection()
        try:
            mis = conn.execute("SELECT misconception FROM attempts WHERE quiz_id=? AND item_id=? ORDER BY ts DESC LIMIT 1",
                               (quiz_id, a["item_id"])).fetchone()[0]
        finally:
            conn.close()
        results.append({"item_id": a["item_id"], "correct": 1.0 if correct else 0.0, "misconception": mis,
                        **item_feedback(a["item_id"])})
    conn = get_connection()
    try:
        conn.execute("UPDATE quizzes SET submitted_at=? WHERE quiz_id=?", (datetime.now(timezone.utc).isoformat(), quiz_id))
        conn.commit()
        updates = [{"concept_id": c, "p_before": b, "p_after": f} for c, b, f in conn.execute(
            "SELECT concept_id, MIN(CASE WHEN rn_first=1 THEN p_before END), MIN(CASE WHEN rn_last=1 THEN p_after END) FROM ("
            " SELECT concept_id, p_before, p_after,"
            " ROW_NUMBER() OVER (PARTITION BY concept_id ORDER BY event_id) rn_first,"
            " ROW_NUMBER() OVER (PARTITION BY concept_id ORDER BY event_id DESC) rn_last"
            " FROM mastery_events WHERE source='quiz' AND ref_id=? AND student_id=?) GROUP BY concept_id", (quiz_id, student_id))]
    finally:
        conn.close()
    return {"quiz_id": quiz_id, "student_id": student_id, "results": results, "mastery_updates": updates}


def quiz_student(quiz_id: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT student_id FROM quizzes WHERE quiz_id=?", (quiz_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None
