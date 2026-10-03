import math

def p_correct_1pl(theta: float, b: float) -> float:
    """Rasch (1PL) Model: Probability of correct answer given ability (theta) and difficulty (b)."""
    return 1.0 / (1.0 + math.exp(-(theta - b)))

def p_correct_2pl(theta: float, a: float, b: float) -> float:
    """2PL Model: Adds discrimination (a) parameter."""
    return 1.0 / (1.0 + math.exp(-a * (theta - b)))

def update_ability(theta: float, b: float, correct: bool, lr: float = 0.1) -> float:
    """Simple gradient update for latent ability theta based on 1PL residual."""
    y = 1.0 if correct else 0.0
    p = p_correct_1pl(theta, b)
    return theta + lr * (y - p)
