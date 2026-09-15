"""Industry packs: expected economics, tax treatment and known failure modes.

Loaded as data rather than written into prompts, so a benchmark can be tested,
versioned and corrected without touching a model.
"""

from .packs import (
    TIER_ORDER,
    BenchmarkComparison,
    CostLine,
    IndustryPack,
    SizeTier,
    load_packs,
    pack_for,
    packs_with_benchmarks,
)
from .ranges import MoneyRange, Range, parse_money_range, parse_percent_range

__all__ = [
    "IndustryPack", "SizeTier", "CostLine", "BenchmarkComparison",
    "load_packs", "pack_for", "packs_with_benchmarks", "TIER_ORDER",
    "Range", "MoneyRange", "parse_percent_range", "parse_money_range",
]
