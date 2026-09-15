"""The deterministic engine: the calculator of record.

Nothing in this package calls a model, touches a network, or reads a prompt.
Every function is a pure transformation of canonical records into numbers that
a human reviewer can reproduce by hand. That property is what allows a language
model's output to be checked rather than trusted.
"""

from .aging import AgingReport, build_aging, concentration, days_sales_outstanding
from .features import (
    find_duplicate_clusters,
    is_round_amount,
    monthly_series,
    period_variance,
    recurring_profiles,
    robust_zscore,
)
from .reconcile import BankStatement, ReconciliationResult, StatementLine, reconcile
from .rollforward import RollForward, build_rollforward, debt_rollforward
from .statements import BalanceSheet, IncomeStatement, build_balance_sheet, build_income_statement
from .trial_balance import AccountBalance, TrialBalance, balance_of, build_trial_balance

__all__ = [
    "AccountBalance", "TrialBalance", "balance_of", "build_trial_balance",
    "IncomeStatement", "BalanceSheet", "build_income_statement", "build_balance_sheet",
    "AgingReport", "build_aging", "days_sales_outstanding", "concentration",
    "BankStatement", "StatementLine", "ReconciliationResult", "reconcile",
    "RollForward", "build_rollforward", "debt_rollforward",
    "monthly_series", "period_variance", "recurring_profiles",
    "find_duplicate_clusters", "is_round_amount", "robust_zscore",
]
