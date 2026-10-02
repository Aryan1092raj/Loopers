"""Cooperative cancellation flag for in-process generation runs.

Python threads can't be killed from the outside, and the LLM calls are
blocking network I/O, so we can't abort mid-request. Instead every LLM call
site checks is_cancelled() right before firing — for a multi-slide/multi-
diagram deck that means a "Stop" click takes effect within roughly one
in-flight call's duration, not instantly, but well before the whole run
finishes.
"""
import threading

_event = threading.Event()


def reset() -> None:
    """Clear the flag — call at the start of a new generation run."""
    _event.clear()


def request_cancel() -> None:
    _event.set()


def is_cancelled() -> bool:
    return _event.is_set()


class GenerationCancelled(Exception):
    """Raised at an LLM call site when a stop was requested mid-run."""
    pass


def check() -> None:
    """Raise GenerationCancelled if a stop was requested. Call before each LLM call."""
    if _event.is_set():
        raise GenerationCancelled("Generation stopped by user")
