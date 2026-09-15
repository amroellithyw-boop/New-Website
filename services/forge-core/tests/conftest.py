"""Shared fixtures. The company is built once per session; it is deterministic."""

from __future__ import annotations

from datetime import date

import pytest

from forge.agents import ModelGateway
from forge.bench.seeder import build_seeded_case
from forge.connectors.fixture_contractor import build_contractor_company
from forge.pipeline import run_continuous_controller
from forge.rules import RuleContext

PERIOD_START = date(2026, 6, 1)
PERIOD_END = date(2026, 6, 30)
FY_START = date(2026, 1, 1)

# Every injector except the unbalanced entry, so the data gate still passes and
# the review hierarchy can be exercised.
GATE_PASSING_ERRORS = [
    "duplicate_bill", "closed_period_posting", "missing_bank_entry", "unapplied_receipt",
    "payment_without_bill", "capitalisable_expense", "untaxed_revenue", "unremitted_payroll",
    "missing_depreciation", "unreversed_accrual", "loan_split_error", "suspense_account",
    "retained_earnings_posting", "unauthorised_poster",
    "decoy_large_legitimate_job", "decoy_recurring_rent_increase",
]


@pytest.fixture(scope="session")
def clean_company():
    return build_contractor_company()


@pytest.fixture()
def clean_ledger(clean_company):
    return clean_company.ledger


@pytest.fixture()
def clean_context(clean_company):
    return RuleContext(
        ledger=clean_company.ledger,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        fiscal_year_start=FY_START,
        statements=tuple(clean_company.statements),
        policy={"authorised_posters": {"sync", "payroll_sync", "close_process", "migration"}},
    ).prepare()


@pytest.fixture()
def seeded_case():
    return build_seeded_case(period_start=PERIOD_START, period_end=PERIOD_END)


@pytest.fixture()
def gate_passing_case():
    return build_seeded_case(
        only=GATE_PASSING_ERRORS, period_start=PERIOD_START, period_end=PERIOD_END,
        name="gate-passing",
    )


@pytest.fixture()
def clean_run(clean_company):
    return run_continuous_controller(
        clean_company.ledger,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        statements=clean_company.statements,
    )


@pytest.fixture()
def offline_gateway():
    return ModelGateway.offline()
