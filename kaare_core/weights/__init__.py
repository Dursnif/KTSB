"""KTSB's one weight system (P149): evidence stored, weights computed; one interface every consumer calls.

    from kaare_core import weights
    weights.record(("sensor", "insightface|cam|ir"), source_class="world", polarity=+1, context=event_id)
    w = weights.get(("sensor", "insightface|cam|ir"))   # RAM only
    weights.answer(subject, by_user="userx", verdict="yes", verbatim=True, context=question_id)

Phase 1 = shadow: nothing acts on these numbers yet.
"""

from kaare_core.weights.model import Weight
from kaare_core.weights.store import WeightStore, get_store


def record(subject, **kw) -> bool:
    return get_store().record(subject, **kw)


def answer(subject, **kw) -> bool:
    return get_store().answer(subject, **kw)


def get(subject, at=None) -> Weight:
    return get_store().get(subject, at=at)


def park(subject, reason: str = "") -> bool:
    return get_store().park(subject, reason)


def unpark(subject, reason: str = "") -> bool:
    return get_store().unpark(subject, reason)


def is_parked(subject) -> bool:
    return get_store().is_parked(subject)


def flush() -> int:
    return get_store().flush()


def flush_ok() -> bool:
    """Flush and say whether everything queued is now on disk (N39: a receipt never says «saved» when it is not). A
    failed flush keeps the rows queued (retried in the background) and returns False."""
    n = get_store().flush()
    return n > 0 or get_store().pending() == 0


__all__ = ["Weight", "WeightStore", "answer", "flush", "flush_ok", "get", "get_store", "is_parked", "park", "record", "unpark"]
