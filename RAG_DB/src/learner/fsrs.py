import datetime

def retrievability(t_days: float, stability: float) -> float:
    """
    FSRS v4 standalone formula for memory retention curve.
    Strict invariant: R(S, S) = 0.90
    """
    if stability <= 0:
        return 0.0
    return 9.0 * stability / (9.0 * stability + t_days)

def next_interval(rating: int, stability: float, difficulty: float, last_review: datetime.datetime | None, current_time: datetime.datetime) -> tuple[datetime.datetime, float, float]:
    """
    Computes the next review date, stability, and difficulty based on rating [1-4].
    Rating 1: Forgot/Incorrect
    Rating > 1: Correct (2=Hard, 3=Good, 4=Easy)
    """
    if last_review is None:
        t_days = 0.0
    else:
        t_days = (current_time - last_review).total_seconds() / 86400.0
        
    # Simplified deterministic FSRS stability/difficulty evolution for tracking
    if rating > 1:  # Correct
        new_d = max(1.0, difficulty - 0.5)
        new_s = max(1.0, stability * 1.5)
    else:           # Incorrect
        new_d = min(10.0, difficulty + 1.0)
        new_s = max(0.1, stability * 0.5)
        
    interval_days = max(1.0, new_s)
    next_rev = current_time + datetime.timedelta(days=interval_days)
    
    return next_rev, new_s, new_d
