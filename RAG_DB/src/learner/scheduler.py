import datetime
import networkx as nx
from concepts.api import get_graph
from core.db import get_connection

def get_next_schedule(student_id: str, limit: int = 5) -> list[dict]:
    """
    Constructs a topological DAG of concepts.
    Unlocks concepts only if all prerequisites are mastered (p_mastery >= 0.70).
    Ranks unlocked concepts by FSRS urgency and BKT mastery gap.
    """
    conn = get_connection()
    
    # 1. Fetch all concepts and edges (the graph seam leaves out concepts of removed sources)
    graph = get_graph(fixture=False)
    G = nx.DiGraph()
    for n in graph["nodes"]:
        G.add_node(n["id"])
    for e in graph["edges"]:
        G.add_edge(e["src"], e["dst"])
        
    # 2. Fetch learner state
    rows = conn.execute("SELECT concept_id, p_mastery, next_review FROM learner_state WHERE student_id=?", (student_id,)).fetchall()
    state_map = {r[0]: {"p_mastery": r[1], "next_review": r[2]} for r in rows}
    
    unlocked_concepts = []
    now = datetime.datetime.now(datetime.timezone.utc)

    for node in G.nodes:
        # Check prerequisites
        preds = list(G.predecessors(node))
        locked = False
        for p in preds:
            p_state = state_map.get(p, {})
            if p_state.get("p_mastery", 0.1) < 0.70:
                locked = True
                break
                
        if not locked:
            unlocked_concepts.append(node)
            
    # 3. Rank unlocked concepts
    # Score = Urgency based on FSRS (is next_review in the past?) and Mastery (1 - p_mastery)
    ranked = []
    
    # Get a stable topological order for tie-breaking
    try:
        topo_order = {node: i for i, node in enumerate(nx.topological_sort(G))}
    except nx.NetworkXUnfeasible:
        # Fallback if cycles exist (should be blocked by DB invariants normally)
        topo_order = {node: 0 for node in G.nodes}
        
    for node in unlocked_concepts:
        st = state_map.get(node, {"p_mastery": 0.0, "next_review": None})
        p = st["p_mastery"]
        nxt = st["next_review"]
        
        # FSRS urgency: if overdue, extremely urgent. If new, high priority.
        is_overdue = False
        nxt_dt = None
        if nxt:
            try:
                nxt_dt = datetime.datetime.fromisoformat(nxt)
                if nxt_dt.tzinfo is None:
                    nxt_dt = nxt_dt.replace(tzinfo=datetime.timezone.utc)
                if now > nxt_dt:
                    is_overdue = True
            except ValueError:
                pass
                
        # Priority mapping
        # 1. Overdue reviews
        # 2. Unstarted/Low mastery
        score = 0
        if is_overdue:
            score += 1000
        score += (1.0 - p) * 100 # Mastery gap
        
        # Tie breaker based on topology (earlier concepts get slightly higher bonus to enforce sequence)
        topo_bonus = -topo_order.get(node, 0)
        
        ranked.append({
            "concept_id": node,
            "p_mastery": p,
            "is_overdue": is_overdue,
            "next_review": nxt_dt,
            "sort_score": score,
            "topo_bonus": topo_bonus
        })
        
    # Sort descending by primary score, then topology
    ranked.sort(key=lambda x: (x["sort_score"], x["topo_bonus"]), reverse=True)
    
    conn.close()
    return ranked[:limit]


WEAK_BELOW = 0.6
LEARN_MIN, REVIEW_MIN = 20, 10
DEFAULT_HORIZON, MAX_HORIZON = 7, 14


def plan_days(student_id: str, minutes_per_day: int = 60, today: datetime.date | None = None) -> list[dict]:
    """
    Day-by-day plan from the ranked concepts. A weak or overdue concept is due today; otherwise it is due on its
    FSRS next_review date. Each day holds at most minutes_per_day, a concept that does not fit moves to the next
    day with room, and nothing is planned past the exam date (or a 7-day horizon without one, 14 at most).
    """
    today = today or datetime.date.today()
    conn = get_connection()
    try:
        row = conn.execute("SELECT exam_date FROM students WHERE student_id=?", (student_id,)).fetchone()
    finally:
        conn.close()
    days_left = None
    if row and row[0]:
        try:
            days_left = (datetime.date.fromisoformat(row[0][:10]) - today).days
        except ValueError:
            pass
    horizon = DEFAULT_HORIZON if days_left is None else max(1, min(days_left, MAX_HORIZON))

    used = [0] * horizon
    plan = [[] for _ in range(horizon)]
    for c in get_next_schedule(student_id, limit=10_000):
        p, nxt = c["p_mastery"], c["next_review"]
        weak = p < WEAK_BELOW
        due = 0 if (c["is_overdue"] or weak or nxt is None) else max(0, (nxt.date() - today).days)
        minutes = min(LEARN_MIN if weak else REVIEW_MIN, minutes_per_day)
        day = next((d for d in range(due, horizon) if used[d] + minutes <= minutes_per_day), None)
        if day is None:
            continue
        used[day] += minutes
        reason = "review overdue" if c["is_overdue"] else f"mastery gap ({p:.0%})" if weak else "scheduled review"
        if days_left is not None:
            reason += f", exam in {days_left} days"
        plan[day].append({"concept_id": c["concept_id"], "minutes": minutes, "reason": reason})
    return [{"date": (today + datetime.timedelta(days=d)).isoformat(), "tasks": t} for d, t in enumerate(plan) if t]
