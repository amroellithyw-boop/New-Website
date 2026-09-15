"""Outcomes become suppressions, known patterns and precedents."""

from .outcomes import (
    SUPPRESS_AFTER,
    LearningPolicy,
    Outcome,
    OutcomeLog,
    apply_learning,
    pattern_key,
)

__all__ = ["SUPPRESS_AFTER", "OutcomeLog", "Outcome", "LearningPolicy", "apply_learning", "pattern_key"]
