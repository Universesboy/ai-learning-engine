"""Mastery scoring.

Ported from DeepTutor `deeptutor/learning/mastery.py` (Apache-2.0, Copyright HKUDS):
recency-weighted accuracy with a low-confidence cap, so one lucky answer cannot "master" a point.
Extended here: application-level evidence is required before a point counts as mastered, because
the brief asks not to judge mastery by recall questions alone.
"""
from __future__ import annotations

_RECENCY_WEIGHTS = (0.5, 0.7, 0.85, 0.95, 1.0)
_CONFIDENCE_CAP = {1: 0.5, 2: 0.8}
MASTERED = 0.8


def compute_mastery(correctness: list[float]) -> float:
    if not correctness:
        return 0.0
    recent = correctness[-len(_RECENCY_WEIGHTS):]
    weights = _RECENCY_WEIGHTS[-len(recent):]
    score = sum(w * max(0.0, min(1.0, float(c))) for c, w in zip(recent, weights)) / sum(weights)
    return min(score, _CONFIDENCE_CAP.get(len(recent), 1.0))


def concept_status(scores_by_level: dict[str, list[float]]) -> dict:
    """levels: recall / understand / apply. Mastered needs overall >= 0.8 AND an apply score >= 0.7."""
    all_scores = [s for lvl in ("recall", "understand", "apply") for s in scores_by_level.get(lvl, [])]
    overall = compute_mastery(all_scores)
    apply_scores = scores_by_level.get("apply", [])
    can_apply = bool(apply_scores) and compute_mastery(apply_scores) >= 0.7
    if not all_scores:
        status = "not_started"
    elif overall >= MASTERED and can_apply:
        status = "mastered"
    elif overall >= MASTERED:
        status = "cannot_apply_yet"
    elif overall >= 0.5:
        status = "shaky"
    else:
        status = "needs_relearning"
    return {"mastery": round(overall, 3), "can_apply": can_apply, "status": status}
