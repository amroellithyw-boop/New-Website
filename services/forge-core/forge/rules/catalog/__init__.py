"""The ForgeOS control catalogue.

Importing this package registers every control. Modules are grouped by the part
of the finance function they protect, not by the order they were written, so a
reviewer can read one file and understand one area completely.
"""

from . import (  # noqa: F401  - imported for the registration side effect
    ap,
    ar,
    benchmarks,
    debt_cash,
    duplicates,
    fixed_assets,
    integrity,
    payroll,
    profitability,
    reconciliation,
    tax,
    variance,
)

__all__ = [
    "integrity", "duplicates", "reconciliation", "variance", "benchmarks",
    "ar", "ap", "tax", "payroll", "fixed_assets", "debt_cash", "profitability",
]
