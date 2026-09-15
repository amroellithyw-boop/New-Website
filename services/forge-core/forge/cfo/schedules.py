"""Schedules a controller signs: capital cost allowance, deferred revenue, loans.

All three existed in the legacy interface as screen logic on floats. Here they
are pure functions on exact money, each returning a schedule that proves its
own arithmetic, so the year-end file can cite them and a control can test them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..money import Money

__all__ = [
    "CCA_CLASSES", "CCAClass", "CCAAsset", "CCAYear", "cca_schedule",
    "DeferredContract", "DeferredPeriod", "deferred_revenue_schedule",
    "LoanTerms", "LoanPeriod", "amortisation_schedule", "reconcile_to_lender",
]


# ---------------------------------------------------------------------------
# Capital cost allowance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CCAClass:
    number: str
    rate: Decimal
    method: str  # "declining" | "straight_line" | "full"
    description: str
    half_year_rule: bool = True


CCA_CLASSES: dict[str, CCAClass] = {
    "1": CCAClass("1", Decimal("0.04"), "declining", "Buildings, brick/concrete"),
    "8": CCAClass("8", Decimal("0.20"), "declining", "Equipment, furniture, tools over $500"),
    "10": CCAClass("10", Decimal("0.30"), "declining", "Vehicles, trucks, powered equipment, trailers"),
    "10.1": CCAClass("10.1", Decimal("0.30"), "declining", "Passenger vehicles over the cost limit; one asset per class"),
    "12": CCAClass("12", Decimal("1.00"), "full", "Small tools under $500, uniforms", half_year_rule=False),
    "13": CCAClass("13", Decimal("0"), "straight_line", "Leasehold improvements"),
    "14.1": CCAClass("14.1", Decimal("0.05"), "declining", "Goodwill and intangibles"),
    "43": CCAClass("43", Decimal("0.30"), "declining", "Manufacturing equipment"),
    "50": CCAClass("50", Decimal("0.55"), "declining", "Computer equipment"),
    "53": CCAClass("53", Decimal("0.50"), "declining", "Manufacturing and processing, acquired 2016 onward"),
}


@dataclass(frozen=True)
class CCAAsset:
    asset_id: str
    description: str
    cca_class: str
    cost: Money
    acquired_on: date
    disposed_on: date | None = None
    proceeds: Money | None = None
    immediate_expensing: bool = False
    """Designated immediate expensing property; the allowance is the full cost
    in the year of acquisition, subject to the annual limit checked elsewhere."""


@dataclass(frozen=True)
class CCAYear:
    asset_id: str
    fiscal_year_end: date
    opening_ucc: Money
    additions: Money
    disposals: Money
    allowance: Money
    closing_ucc: Money
    half_year_applied: bool

    @property
    def ties(self) -> bool:
        return self.opening_ucc + self.additions - self.disposals - self.allowance == self.closing_ucc


def cca_schedule(asset: CCAAsset, fiscal_year_ends: Sequence[date]) -> list[CCAYear]:
    """Declining-balance schedule with the half-year rule in the year of purchase.

    Class 12 claims the full cost with no half-year rule. Straight-line classes
    need a term and are not computed here; they return no rows rather than a
    wrong number.
    """
    klass = CCA_CLASSES.get(asset.cca_class)
    if klass is None or klass.method == "straight_line":
        return []
    rows: list[CCAYear] = []
    ucc = Money.zero(asset.cost.currency)
    for fye in sorted(fiscal_year_ends):
        fy_start = fye.replace(year=fye.year - 1) + timedelta(days=1)
        additions = asset.cost if fy_start <= asset.acquired_on <= fye else Money.zero(asset.cost.currency)
        disposals = Money.zero(asset.cost.currency)
        if asset.disposed_on and fy_start <= asset.disposed_on <= fye:
            proceeds = asset.proceeds or Money.zero(asset.cost.currency)
            disposals = min(proceeds, asset.cost)  # lesser of proceeds and cost
        base = ucc + additions - disposals
        if base.minor_units <= 0:
            rows.append(CCAYear(asset.asset_id, fye, ucc, additions, disposals, Money.zero(asset.cost.currency), base, False))
            ucc = base if base.minor_units > 0 else Money.zero(asset.cost.currency)
            continue
        half = klass.half_year_rule and not additions.is_zero and not asset.immediate_expensing
        if asset.immediate_expensing and not additions.is_zero:
            allowance = additions + (ucc - disposals).scale(klass.rate) if (ucc - disposals).minor_units > 0 else additions
        elif klass.method == "full":
            allowance = base
        else:
            eligible = (ucc - disposals) + (additions.scale(Decimal("0.5")) if half else additions)
            allowance = eligible.scale(klass.rate) if eligible.minor_units > 0 else Money.zero(asset.cost.currency)
        if allowance > base:
            allowance = base
        closing = base - allowance
        rows.append(CCAYear(asset.asset_id, fye, ucc, additions, disposals, allowance, closing, half))
        ucc = closing
    return rows


# ---------------------------------------------------------------------------
# Deferred revenue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeferredContract:
    contract_id: str
    customer: str
    total: Money
    service_start: date
    service_end: date
    received_on: date


@dataclass(frozen=True)
class DeferredPeriod:
    contract_id: str
    month: date  # first of month
    recognised: Money
    cumulative: Money
    remaining: Money


def _months(start: date, end: date) -> list[date]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(date(y, m, 1))
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def deferred_revenue_schedule(contract: DeferredContract) -> list[DeferredPeriod]:
    """Recognise a prepaid contract ratably over its service months.

    Uses largest-remainder allocation so the periods sum exactly to the contract
    total; the last month never carries a rounding plug.
    """
    months = _months(contract.service_start, contract.service_end)
    if not months:
        return []
    parts = contract.total.allocate([1] * len(months))
    out: list[DeferredPeriod] = []
    cumulative = Money.zero(contract.total.currency)
    for month, part in zip(months, parts, strict=True):
        cumulative = cumulative + part
        out.append(DeferredPeriod(contract.contract_id, month, part, cumulative, contract.total - cumulative))
    return out


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoanTerms:
    loan_id: str
    lender: str
    principal: Money
    annual_rate: Decimal
    payments_per_year: int
    term_payments: int
    first_payment: date
    payment: Money | None = None
    """Fixed payment; computed from the terms when omitted."""


@dataclass(frozen=True)
class LoanPeriod:
    number: int
    due_on: date
    opening: Money
    payment: Money
    interest: Money
    principal: Money
    closing: Money


def _add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def level_payment(principal: Money, annual_rate: Decimal, payments_per_year: int, n: int) -> Money:
    r = annual_rate / Decimal(payments_per_year)
    if r == 0:
        return principal.scale(Decimal(1) / Decimal(n))
    factor = r / (Decimal(1) - (Decimal(1) + r) ** (-n))
    return principal.scale(factor)


def amortisation_schedule(terms: LoanTerms) -> list[LoanPeriod]:
    """Level-payment schedule; the final period absorbs the rounding to zero."""
    payment = terms.payment or level_payment(terms.principal, terms.annual_rate, terms.payments_per_year, terms.term_payments)
    rate = terms.annual_rate / Decimal(terms.payments_per_year)
    step_months = 12 // terms.payments_per_year if terms.payments_per_year in (1, 2, 3, 4, 6, 12) else None
    rows: list[LoanPeriod] = []
    balance = terms.principal
    for k in range(1, terms.term_payments + 1):
        if balance.minor_units <= 0:
            break
        interest = balance.scale(rate, rounding=ROUND_HALF_UP)
        principal = payment - interest
        if k == terms.term_payments or principal >= balance:
            principal = balance
            payment_k = principal + interest
        else:
            payment_k = payment
        closing = balance - principal
        if step_months:
            due = _add_months(terms.first_payment, (k - 1) * step_months)
        else:
            due = terms.first_payment + timedelta(days=round(365 * (k - 1) / terms.payments_per_year))
        rows.append(LoanPeriod(k, due, balance, payment_k, interest, principal, closing))
        balance = closing
    return rows


@dataclass(frozen=True)
class LenderReconciliation:
    as_of: date
    book_balance: Money
    lender_balance: Money
    scheduled_balance: Money
    payment: Money

    @property
    def lender_vs_schedule(self) -> Money:
        return self.lender_balance - self.scheduled_balance

    @property
    def missed_payments_estimate(self) -> int:
        """How many scheduled payments the lender's balance implies were missed."""
        diff = self.lender_vs_schedule
        if diff.minor_units <= 0 or self.payment.minor_units <= 0:
            return 0
        return int((Decimal(diff.minor_units) / Decimal(self.payment.minor_units)).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    @property
    def book_vs_lender(self) -> Money:
        return self.book_balance - self.lender_balance


def reconcile_to_lender(schedule: Sequence[LoanPeriod], as_of: date, *, book_balance: Money, lender_balance: Money) -> LenderReconciliation:
    """Compare books and lender against the schedule at a date.

    A lender balance above the schedule means payments were missed; books above
    the lender means payments were paid but coded elsewhere. Both are findings.
    """
    paid = [p for p in schedule if p.due_on <= as_of]
    scheduled = paid[-1].closing if paid else (schedule[0].opening if schedule else Money.zero())
    payment = schedule[0].payment if schedule else Money.zero()
    return LenderReconciliation(as_of, book_balance, lender_balance, scheduled, payment)
