import datetime
import networkx as nx
from core.db import get_connection

def get_next_schedule(student_id: str, limit: int = 5) -> list[dict]:
    """
    Constructs a topological DAG of concepts.
    Unlocks concepts only if all prerequisites are mastered (p_mastery >= 0.70).
    Ranks unlocked concepts by FSRS urgency and BKT mastery gap.
    """
    conn = get_connection()
    
    # 1. Fetch all concepts and edges
    concepts = conn.execute("SELECT concept_id FROM concepts").fetchall()
    edges = conn.execute("SELECT src, dst FROM concept_edges").fetchall()
    
    G = nx.DiGraph()
    for (cid,) in concepts:
        G.add_node(cid)
    for src, tgt in edges:
        G.add_edge(src, tgt)
        
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
        if nxt:
            try:
                nxt_dt = datetime.datetime.fromisoformat(nxt)
                if nxt_dt.tzinfo is None:
                    nxt_dt = nxt_dt.replace(tzinfo=datetime.timezone.utc)
                if now > nxt_dt:
                    is_overdue = True
            except:
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
            "sort_score": score,
            "topo_bonus": topo_bonus
        })
        
    # Sort descending by primary score, then topology
    ranked.sort(key=lambda x: (x["sort_score"], x["topo_bonus"]), reverse=True)
    
    conn.close()
    return ranked[:limit]
