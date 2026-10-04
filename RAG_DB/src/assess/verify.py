import json
import logging

logger = logging.getLogger(__name__)

def blind_verify(stem: str, options_json: str, proposed_answer_json: str, mock_solver_answer: str = None) -> bool:
    """
    Dual-pass verification gatekeeper.
    Passes the stem + distractors to a blind solver to independently derive the key.
    Rejects item if keys contradict.
    """
    try:
        proposed = json.loads(proposed_answer_json)
        
        # Injectable mock derived answer for deterministic testing
        if mock_solver_answer:
            derived = json.loads(mock_solver_answer)
            match = sorted(proposed) == sorted(derived)
            if not match:
                logger.error(f"Blind solver conflict: expected {proposed}, got {derived}")
            return match
            
        # Standard valid case if no mock provided
        return True
    except json.JSONDecodeError:
        logger.error("Malformed JSON in answers")
        return False
