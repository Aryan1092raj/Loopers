def update_p_know(p_know: float, p_slip: float, p_guess: float, p_trans: float, correct: bool) -> float:
    """
    Standard Corbett & Anderson Bayesian Knowledge Tracing (BKT).
    Updates the latent probability of mastery (p_know) given a binary observation.
    Enforces monotonicity and prevents floating-point degeneracy by clipping.
    """
    if correct:
        numerator = p_know * (1.0 - p_slip)
        denominator = numerator + (1.0 - p_know) * p_guess
    else:
        numerator = p_know * p_slip
        denominator = numerator + (1.0 - p_know) * (1.0 - p_guess)
        
    p_L_obs = (numerator / denominator) if denominator > 0 else 0.0
    p_next = p_L_obs + (1.0 - p_L_obs) * p_trans
    
    eps = 1e-4
    return max(eps, min(1.0 - eps, p_next))
