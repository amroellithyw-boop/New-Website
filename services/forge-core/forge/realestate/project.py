"""Where the build stands and what cash it will need.

Straight-line spend over the remaining months is a deliberate simplification:
it is wrong in detail and right in total, and it is replaced by the lender's
draw schedule and the trade contracts as Phase 2 records them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..money import Money
from .facts import EngagementFacts

__all__ = ["ProjectStatus", "CashMonth", "project_status", "cash_requirement_schedule"]


def _months_between(a: date, b: date) -> int:
    return max((b.year - a.year) * 12 + (b.month - a.month), 0)


@dataclass(frozen=True)
class ProjectStatus:
    as_of: date
    budget: Money
    spent: Money
    percent_spent: Decimal
    cost_to_complete: Money
    months_remaining: int
    monthly_burn_required: Money
    facility_limit: Money
    drawn: Money
    undrawn: Money
    owner_equity_required: Money
    total_project_cost: Money
    appraised_value: Money | None
    loan_to_cost: Decimal | None
    loan_to_value: Decimal | None
    equity_created: Money | None
    capitalised_interest_estimate: Money
    notes: tuple[str, ...]


def project_status(facts: EngagementFacts, *, today: date) -> ProjectStatus:
    cur = facts.currency
    budget = facts.budget_mid or Money.zero(cur)
    spent = facts.spent_to_date or Money.zero(cur)
    pct = (Decimal(spent.minor_units) / Decimal(budget.minor_units)) if budget.minor_units else Decimal(0)
    ctc = max(budget - spent, Money.zero(cur))
    end = facts.expected_completion or today
    months = max(_months_between(today, end), 1)
    burn = ctc.scale(Decimal(1) / Decimal(months))
    limit, drawn = facts.total_facility_limit, facts.total_drawn
    undrawn = max(limit - drawn, Money.zero(cur))
    equity_needed = max(ctc - undrawn, Money.zero(cur))
    land = (facts.purchase_price or Money.zero(cur)) + facts.acquisition_costs
    schedule = cash_requirement_schedule(facts, today=today)
    interest = Money.zero(cur)
    for m in schedule:
        interest = interest + m.interest
    total_cost = land + budget + interest
    ltc = (Decimal(limit.minor_units) / Decimal(total_cost.minor_units)) if total_cost.minor_units else None
    ltv = (Decimal(limit.minor_units) / Decimal(facts.appraised_completed_value.minor_units)) if facts.appraised_completed_value and facts.appraised_completed_value.minor_units else None
    created = (facts.appraised_completed_value - total_cost) if facts.appraised_completed_value else None
    notes = (
        "Cost to complete is the midpoint budget less spend to date; the budget is the client's figure and is not yet a bill of quantities.",
        "Interest during construction is capitalised to the building (ITA s.18(3.1)) and is estimated on the drawn balance at the stated rate.",
        "Owner equity required is what the facilities do not cover; it is due as construction outpaces the draw limit.",
    )
    return ProjectStatus(today, budget, spent, pct, ctc, months, burn, limit, drawn, undrawn, equity_needed, total_cost,
                         facts.appraised_completed_value, ltc, ltv, created, interest, notes)


@dataclass(frozen=True)
class CashMonth:
    month: date
    spend: Money
    draw: Money
    owner_contribution: Money
    interest: Money
    facility_balance: Money


def cash_requirement_schedule(facts: EngagementFacts, *, today: date) -> list[CashMonth]:
    cur = facts.currency
    budget = facts.budget_mid or Money.zero(cur)
    spent = facts.spent_to_date or Money.zero(cur)
    ctc = max(budget - spent, Money.zero(cur))
    end = facts.expected_completion or today
    months = max(_months_between(today, end), 1)
    monthly = ctc.allocate([1] * months)
    limit, balance = facts.total_facility_limit, facts.total_drawn
    rate = facts.facilities[0].annual_rate if facts.facilities else Decimal("0")
    out: list[CashMonth] = []
    m = date(today.year, today.month, 1)
    for spend in monthly:
        interest = balance.scale(rate / Decimal(12))
        need = spend + interest
        room = max(limit - balance, Money.zero(cur))
        draw = min(need, room)
        owner = need - draw
        balance = balance + draw
        out.append(CashMonth(m, spend, draw, owner, interest, balance))
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out
