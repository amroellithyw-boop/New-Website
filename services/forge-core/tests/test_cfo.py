"""The CFO layer: schedules that prove themselves, and scores built on proven facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from forge.cfo import (
    CCAAsset,
    DeferredContract,
    LoanTerms,
    amortisation_schedule,
    cash_forecast,
    cca_schedule,
    deferred_revenue_schedule,
    health_score,
    reconcile_to_lender,
    working_capital,
)
from forge.money import Money, msum
from forge.pipeline import DEFAULT_POLICY
from forge.rules import RuleContext, run_rules
from tests.conftest import FY_START, PERIOD_END, PERIOD_START


def _ctx(company, naics=None):
    policy = dict(DEFAULT_POLICY)
    if naics:
        policy["naics_code"] = naics
    return RuleContext(ledger=company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                       fiscal_year_start=FY_START, statements=tuple(company.statements), policy=policy).prepare()


class TestCCA:
    def test_half_year_rule_applies_in_the_year_of_purchase(self):
        asset = CCAAsset("A1", "Skid steer", "10", Money.from_decimal("60000.00"), date(2025, 5, 1))
        rows = cca_schedule(asset, [date(2025, 12, 31), date(2026, 12, 31)])
        assert rows[0].half_year_applied
        assert rows[0].allowance == Money.from_decimal("9000.00")   # 60000 x 50% x 30%
        assert not rows[1].half_year_applied
        assert rows[1].allowance == Money.from_decimal("15300.00")  # 51000 x 30%
        assert all(r.ties for r in rows)

    def test_class_12_claims_the_full_cost(self):
        asset = CCAAsset("T1", "Small tools", "12", Money.from_decimal("450.00"), date(2025, 3, 1))
        rows = cca_schedule(asset, [date(2025, 12, 31)])
        assert rows[0].allowance == asset.cost and rows[0].closing_ucc.is_zero

    def test_disposal_reduces_ucc_by_the_lesser_of_proceeds_and_cost(self):
        asset = CCAAsset("A2", "Truck", "10", Money.from_decimal("40000.00"), date(2024, 1, 15),
                         disposed_on=date(2026, 3, 1), proceeds=Money.from_decimal("50000.00"))
        rows = cca_schedule(asset, [date(2024, 12, 31), date(2025, 12, 31), date(2026, 12, 31)])
        assert rows[2].disposals == asset.cost
        assert rows[2].closing_ucc.minor_units <= 0 or rows[2].allowance.is_zero

    def test_straight_line_classes_return_nothing_rather_than_a_wrong_number(self):
        asset = CCAAsset("L1", "Leasehold", "13", Money.from_decimal("10000.00"), date(2025, 1, 1))
        assert cca_schedule(asset, [date(2025, 12, 31)]) == []


class TestDeferredRevenue:
    def test_ratable_recognition_sums_exactly_to_the_contract(self):
        c = DeferredContract("C1", "Harbourfront", Money.from_decimal("10000.00"),
                             date(2025, 11, 15), date(2026, 4, 15), date(2025, 10, 20))
        sched = deferred_revenue_schedule(c)
        assert len(sched) == 6  # Nov through Apr
        assert msum((p.recognised for p in sched), "CAD") == c.total
        assert sched[-1].remaining.is_zero

    def test_no_rounding_plug_in_the_last_month(self):
        c = DeferredContract("C2", "X", Money.from_decimal("100.00"), date(2026, 1, 1), date(2026, 3, 31), date(2025, 12, 1))
        sched = deferred_revenue_schedule(c)
        amounts = sorted(p.recognised.minor_units for p in sched)
        assert amounts[-1] - amounts[0] <= 1


class TestLoans:
    def test_schedule_amortises_to_zero(self):
        terms = LoanTerms("L1", "Bank", Money.from_decimal("100000.00"), Decimal("0.06"), 12, 60, date(2026, 1, 1))
        rows = amortisation_schedule(terms)
        assert len(rows) == 60
        assert rows[-1].closing.is_zero
        for r in rows:
            assert r.opening - r.principal == r.closing
            assert r.interest + r.principal == r.payment

    def test_level_payment_is_close_to_the_textbook_figure(self):
        terms = LoanTerms("L1", "Bank", Money.from_decimal("100000.00"), Decimal("0.06"), 12, 60, date(2026, 1, 1))
        rows = amortisation_schedule(terms)
        assert Money.from_decimal("1930.00") < rows[0].payment < Money.from_decimal("1937.00")

    def test_lender_reconciliation_estimates_missed_payments(self):
        terms = LoanTerms("L1", "Bank", Money.from_decimal("100000.00"), Decimal("0.06"), 12, 60, date(2026, 1, 1))
        rows = amortisation_schedule(terms)
        as_of = date(2026, 6, 15)
        scheduled = [r for r in rows if r.due_on <= as_of][-1].closing
        two_missed = scheduled + rows[0].payment.scale(2)
        rec = reconcile_to_lender(rows, as_of, book_balance=scheduled, lender_balance=two_missed)
        assert rec.missed_payments_estimate == 2


class TestScorecardAndHealth:
    def test_working_capital_agrees_with_the_engine(self, clean_company):
        ctx = _ctx(clean_company)
        wc = working_capital(ctx)
        assert wc.receivables == ctx.ar.total
        assert wc.net_working_capital == ctx.bs.working_capital
        assert wc.dso is not None and 30 < wc.dso < 120
        assert wc.current_ratio is not None

    def test_health_score_is_bounded_and_explained(self, clean_company):
        ctx = _ctx(clean_company, "561730")
        outcome = run_rules(ctx)
        hs = health_score(ctx, findings=outcome.findings)
        assert 0 <= hs.score <= 100
        assert hs.grade in "ABCDF"
        assert sum(f.maximum for f in hs.factors) == 100
        assert all(f.detail for f in hs.factors)

    def test_broken_books_score_zero_on_integrity(self, seeded_case):
        ctx = RuleContext(ledger=seeded_case.ledger, period_start=seeded_case.period_start,
                          period_end=seeded_case.period_end, fiscal_year_start=seeded_case.fiscal_year_start,
                          statements=tuple(seeded_case.company.statements), policy=DEFAULT_POLICY).prepare()
        hs = health_score(ctx, findings=run_rules(ctx).findings)
        integrity = next(f for f in hs.factors if f.label == "Data integrity")
        assert integrity.points == 0


class TestCashForecast:
    def test_thirteen_weeks_chain_and_state_assumptions(self, clean_company):
        fc = cash_forecast(_ctx(clean_company))
        assert len(fc.weeks) == 13
        assert fc.weeks[0].opening == fc.opening_cash
        for a, b in zip(fc.weeks, fc.weeks[1:], strict=False):
            assert a.closing == b.opening
        for w in fc.weeks:
            assert w.closing == w.opening + w.receipts - w.disbursements
            assert w.assumptions

    def test_receipts_never_exceed_open_receivables(self, clean_company):
        ctx = _ctx(clean_company)
        fc = cash_forecast(ctx)
        assert msum((w.receipts for w in fc.weeks), "CAD") <= ctx.ar.total

    def test_payroll_is_forecast_when_runs_exist(self, clean_company):
        fc = cash_forecast(_ctx(clean_company))
        assert msum((w.payroll for w in fc.weeks), "CAD").minor_units > 0
