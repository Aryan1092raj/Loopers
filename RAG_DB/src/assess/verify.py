import json
import logging
import re

from rapidfuzz import fuzz

from tutor.answer import NoProvider, complete

logger = logging.getLogger(__name__)

SOLVER_SYSTEM = (
    "Answer the exam question yourself. Reply with a single JSON object {\"answer\": \"...\"} and nothing else. "
    "For multiple choice, give the exact text of the one option you pick. For numeric, give only the number."
)
SHORT_MATCH = 60  # fuzzy match: key vs blind solver, and the grader's fallback when no LLM answers
GRADER_SYSTEM = (
    "You grade a student's short answer against the answer key. Mark it correct if it states the same idea, "
    "even in other words. Mark it wrong if it contradicts the key, misses its main point, or reuses the key's "
    "words with a different meaning. Reply with a single JSON object {\"correct\": true} or {\"correct\": false}."
)


def grade_short(stem: str, expected: str, given: str, grader=None) -> bool:
    """A judge model, not the generator (CLAUDE.md rule 7), decides if a short answer means the same as the key.
    Word overlap alone accepts answers that reuse the key's terms with the wrong meaning."""
    if not given.strip():
        return False
    try:
        reply = (grader or (lambda q: complete([{"role": "system", "content": GRADER_SYSTEM}, {"role": "user", "content": q}],
                                               role="solver", max_tokens=300)))(
            f"Question: {stem}\nAnswer key: {expected}\nStudent answer: {given}")
        return json.loads(re.search(r"\{.*\}", reply, re.S).group(0))["correct"] is True
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError, NoProvider) as e:
        logger.warning(f"Short-answer grader unavailable, falling back to fuzzy match: {e!r}")
        return same_answer("short", expected, given)


def check_answer(item_type: str, stem: str, expected: str, given: str, tolerance: float | None = None) -> bool:
    """Grade a student's answer: the judge model for short answers, exact or numeric match otherwise."""
    if item_type == "short":
        return grade_short(stem, expected, given)
    return same_answer(item_type, expected, given, tolerance)


def same_answer(item_type: str, expected: str, given: str, tolerance: float | None = None) -> bool:
    if item_type == "numeric":
        try:
            e, g = float(expected), float(given)
        except ValueError:
            return False
        return abs(e - g) <= abs(e) * (tolerance or 0.01) + 1e-9
    if item_type == "short":
        return fuzz.token_set_ratio(expected.lower(), given.lower()) >= SHORT_MATCH
    return expected.strip().lower() == given.strip().lower()


def blind_verify(stem: str, options_json: str | None, proposed_answer_json: str, item_type: str = "mcq",
                 tolerance: float | None = None, solver=None) -> bool:
    """A second model, shown only the stem and options, must reach the proposed key. role="solver" routes to a
    different provider than the generator (CLAUDE.md rule 7). No solver available means the item is not verified."""
    try:
        proposed = json.loads(proposed_answer_json)[0]
        question = stem + ("\nOptions:\n" + "\n".join(f"- {o}" for o in json.loads(options_json)) if options_json else "")
        reply = (solver or (lambda q: complete([{"role": "system", "content": SOLVER_SYSTEM}, {"role": "user", "content": q}],
                                               role="solver", max_tokens=1500)))(question)
        m = re.search(r"\{.*\}", reply, re.S)
        derived = str(json.loads(m.group(0))["answer"])
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError, NoProvider) as e:
        logger.error(f"Blind verification could not run: {e!r}")
        return False
    ok = same_answer(item_type, proposed, derived, tolerance)
    if not ok:
        logger.error(f"Blind solver conflict: key {proposed!r}, solver {derived!r}")
    return ok
