"""The deterministic engine is the calculator of record. It has to tie, exactly."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from forge.engine import (
    build_aging,
    build_balance_sheet,
    build_income_statement,
    build_rollforward,
    build_trial_balance,
    reconcile,
)
from forge.engine.rollforward import debt_rollforward
from forge.money import Money, msum
from tests.conftest import FY_START, PERIOD_END, PERIOD_START


def test_trial_balance_nets_to_zero(clean_ledger):
    tb = build_trial_balance(clean_ledger, PERIOD_START, PERIOD_END)
    assert tb.closing_imbalance.is_zero
    assert tb.activity_imbalance.is_zero
    assert tb.total_debits == tb.total_credits


def test_no_unbalanced_entries_in_a_generated_book(clean_ledger):
    tb = build_trial_balance(clean_ledger, date(2025, 1, 1), PERIOD_END)
    assert tb.unbalanced_transactions == ()


def test_balance_sheet_equation_holds_exactly(clean_ledger):
    bs = build_balance_sheet(clean_ledger, PERIOD_END, fiscal_year_start=FY_START)
    assert bs.equation_difference.is_zero, (
        "assets must equal liabilities plus equity; a non-zero difference here means "
        "an account is missing from the statement mapping or a contra is flipped"
    )


@pytest.mark.parametrize("as_of", [date(2025, 6, 30), date(2025, 12, 31), date(2026, 3, 31)])
def test_balance_sheet_balances_on_any_date(clean_ledger, as_of):
    bs = build_balance_sheet(
        clean_ledger, as_of, fiscal_year_start=date(as_of.year, 1, 1)
    )
    assert bs.equation_difference.is_zero


def test_income_statement_uses_period_movement_not_closing_balance(clean_ledger):
    """A P&L built from closing balances reports inception-to-date by accident."""
    full = build_income_statement(build_trial_balance(clean_ledger, date(1900, 1, 1), PERIOD_END))
    first = build_income_statement(
        build_trial_balance(clean_ledger, date(1900, 1, 1), date(2025, 12, 31))
    )
    second = build_income_statement(
        build_trial_balance(clean_ledger, date(2026, 1, 1), PERIOD_END)
    )
    assert first.net_income + second.net_income == full.net_income


def test_income_statement_sections_do_not_overlap(clean_ledger):
    tb = build_trial_balance(clean_ledger, FY_START, PERIOD_END)
    pl = build_income_statement(tb)
    seen: set[str] = set()
    for section in pl.sections:
        for row in section.rows:
            assert row.account.account_id not in seen, "an account appears in two sections"
            seen.add(row.account.account_id)


def test_gross_margin_is_plausible_for_a_contractor(clean_ledger):
    tb = build_trial_balance(clean_ledger, date(2025, 7, 1), PERIOD_END)
    pl = build_income_statement(tb)
    assert pl.gross_margin is not None
    assert Decimal("0.20") < pl.gross_margin < Decimal("0.55")


def test_accumulated_depreciation_reduces_assets(clean_ledger):
    """A contra-asset must stay negative inside the asset section."""
    bs = build_balance_sheet(clean_ledger, PERIOD_END, fiscal_year_start=FY_START)
    accum = next(r for r in bs.fixed_assets.rows if r.account.account_id == "1590")
    assert bs.fixed_assets.value_of(accum).minor_units < 0
    gross = msum(
        (
            bs.fixed_assets.value_of(r)
            for r in bs.fixed_assets.rows
            if r.account.account_id != "1590"
        ),
        "CAD",
    )
    assert bs.fixed_assets.total < gross


def test_aging_ties_to_the_receivables_control_account(clean_ledger):
    ar = build_aging(clean_ledger, PERIOD_END, "receivable")
    tb = build_trial_balance(clean_ledger, PERIOD_START, PERIOD_END)
    control = tb.row("1200")
    assert control is not None
    assert ar.total == control.presentation_balance, (
        "the receivables subledger must agree with its control account"
    )


def test_aging_buckets_partition_the_total(clean_ledger):
    ar = build_aging(clean_ledger, PERIOD_END, "receivable")
    assert msum((b.total for b in ar.buckets), "CAD") == ar.total
    assert sum(b.count for b in ar.buckets) == len(ar.items)


def test_backdated_payment_reages_history(clean_ledger):
    early = build_aging(clean_ledger, date(2025, 6, 30), "receivable")
    later = build_aging(clean_ledger, PERIOD_END, "receivable")
    assert early.as_of != later.as_of
    assert early.total != later.total


def test_every_bank_statement_reconciles(clean_company):
    unexplained = []
    for statement in clean_company.statements:
        result = reconcile(clean_company.ledger, statement)
        if not result.is_reconciled:
            unexplained.append((statement.bank_account_id, statement.start))
    assert unexplained == [], f"statements that do not reconcile: {unexplained}"


def test_reconciliation_difference_is_explained_by_outstanding_items(clean_company):
    statement = next(
        s for s in clean_company.statements
        if s.bank_account_id == "BANK-CHQ" and s.start == date(2026, 5, 1)
    )
    result = reconcile(clean_company.ledger, statement)
    assert result.unexplained_difference.is_zero
    # difference = outstanding items, with the sign convention proved
    assert result.difference + result.unmatched_ledger_total == result.unmatched_statement_total


def test_reconciliation_detects_a_bank_only_item(clean_company):
    from forge.engine.reconcile import BankStatement, StatementLine

    statement = next(
        s for s in clean_company.statements
        if s.bank_account_id == "BANK-CHQ" and s.start == date(2026, 5, 1)
    )
    ghost = StatementLine(
        statement_line_id="ST-GHOST",
        posted_on=statement.end - timedelta(days=2),
        amount=Money.from_decimal("-4321.00"),
        description="UNRECORDED WITHDRAWAL",
    )
    tampered = BankStatement(
        bank_account_id=statement.bank_account_id, start=statement.start, end=statement.end,
        opening_balance=statement.opening_balance,
        closing_balance=statement.closing_balance + ghost.amount,
        lines=statement.lines + (ghost,),
    )
    result = reconcile(clean_company.ledger, tampered)
    assert not result.is_reconciled
    assert any(line.statement_line_id == "ST-GHOST" for line in result.bank_only_items)


def test_rollforward_always_ties(clean_ledger):
    for account_id in ("1000", "1200", "1500", "2000", "2700"):
        rf = build_rollforward(clean_ledger, account_id, PERIOD_START, PERIOD_END)
        assert rf.ties, f"{account_id} roll-forward does not tie: {rf.difference}"


def test_debt_rollforward_splits_principal_and_interest(clean_ledger):
    rf = debt_rollforward(clean_ledger, "DEBT-EQUIP", PERIOD_START, PERIOD_END)
    assert rf.ties
    assert rf.principal_repaid.minor_units > 0
    assert rf.interest_expensed.minor_units > 0
    assert rf.implied_split_difference.is_zero, (
        "cash paid must equal principal plus interest in a correctly coded file"
    )
