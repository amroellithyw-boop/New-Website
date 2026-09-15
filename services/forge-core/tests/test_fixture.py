"""The synthetic company must stay realistic, or every test above proves nothing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from forge.canonical.enums import TxnType
from forge.connectors.fixture_contractor import build_contractor_company
from forge.engine import build_income_statement, build_trial_balance
from forge.engine.aging import days_sales_outstanding


def test_generation_is_deterministic():
    a = build_contractor_company()
    b = build_contractor_company()
    assert len(a.ledger.transactions) == len(b.ledger.transactions)
    assert [t.txn_id for t in a.ledger.transactions] == [t.txn_id for t in b.ledger.transactions]
    assert [t.lines[0].amount for t in a.ledger.transactions[:50]] == [
        t.lines[0].amount for t in b.ledger.transactions[:50]
    ]


def test_the_book_has_enough_volume_to_be_a_real_test(clean_ledger):
    assert len(clean_ledger.transactions) > 800
    assert len(clean_ledger.open_items) > 250
    assert len(clean_ledger.applications) > 200


def test_every_generated_entry_balances(clean_ledger):
    unbalanced = [t.txn_id for t in clean_ledger.transactions if not t.is_balanced]
    assert unbalanced == []


def test_every_record_carries_lineage(clean_ledger):
    for txn in clean_ledger.transactions:
        assert txn.lineage is not None, f"{txn.txn_id} has no lineage"
        assert txn.lineage.source_system
        assert txn.lineage.source_id


def test_business_economics_are_plausible(clean_ledger):
    tb = build_trial_balance(clean_ledger, date(2025, 7, 1), date(2026, 6, 30))
    pl = build_income_statement(tb)
    revenue = pl.revenue.total.to_decimal()
    assert Decimal("1000000") < revenue < Decimal("4000000"), "not an SMB contractor"
    assert Decimal("0.20") < pl.gross_margin < Decimal("0.55")
    assert Decimal("-0.05") < pl.net_margin < Decimal("0.25")


def test_seasonality_is_present(clean_ledger):
    """Snow revenue in winter, hardscape in summer. Without this the variance
    controls are never tested against the thing that actually causes noise."""
    winter = build_trial_balance(clean_ledger, date(2026, 1, 1), date(2026, 2, 28))
    summer = build_trial_balance(clean_ledger, date(2025, 6, 1), date(2025, 8, 31))
    snow_winter = winter.row("4010").presentation_movement
    snow_summer = summer.row("4010").presentation_movement
    hard_summer = summer.row("4000").presentation_movement
    assert snow_winter > snow_summer
    assert hard_summer > snow_summer


def test_receivables_profile_is_realistic(clean_ledger):
    from forge.engine import build_aging

    ar = build_aging(clean_ledger, date(2026, 6, 30), "receivable")
    tb = build_trial_balance(clean_ledger, date(2025, 7, 1), date(2026, 6, 30))
    pl = build_income_statement(tb)
    dso = days_sales_outstanding(ar.total, pl.revenue.total, 365)
    assert dso is not None
    assert 30 < dso < 120, f"days sales outstanding of {dso} is not a real contractor"


def test_payroll_runs_semi_monthly(clean_ledger):
    runs = [
        t for t in clean_ledger.transactions
        if t.type is TxnType.PAYROLL and date(2026, 1, 1) <= t.txn_date <= date(2026, 6, 30)
    ]
    assert len(runs) == 12


def test_sales_tax_is_applied_to_revenue(clean_ledger):
    invoices = [t for t in clean_ledger.transactions if t.type is TxnType.INVOICE]
    taxed = [t for t in invoices if any(line.tax_amount for line in t.lines)]
    assert len(taxed) / len(invoices) > 0.95
