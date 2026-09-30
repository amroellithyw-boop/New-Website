"""Rental operations: the pro forma a lender reads and the tax a non-resident pays.

Capital cost allowance on a rental property cannot create or increase a
rental loss (Reg. 1100(11)); the claim is limited to net rental income
before CCA. New purpose-built rental that began construction after 15 April
2024 was proposed for a 10% rate in place of Class 1's 4%; the plan carries
both and flags the enhanced rate as one to confirm as enacted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import Money
from .facts import EngagementFacts
from .ownership import section_216_tax

__all__ = ["RentalProForma", "rental_pro_forma", "section_216_withholding", "cca_claim_limited_by_rental_income", "level_payment"]

VACANCY = Decimal("0.04")
CLASS_1_RATE = Decimal("0.04")
PBRH_ACCELERATED_RATE = Decimal("0.10")
PERMANENT_AMORTISATION_YEARS = 25
LENDER_COVERAGE_TARGET = Decimal("1.20")
SOURCES = (
    "Income Tax Regulations 1100(11): rental property CCA limited to net rental income",
    "Class 1 (4%); Budget 2024 accelerated 10% CCA for purpose-built rental beginning construction after 15 April 2024 and before 2031, available for use before 2036",
    "ITA s.216 election; Part XIII 25% on gross rent absent an NR6",
)


def level_payment(principal: Money, annual_rate: Decimal, years: int) -> Money:
    """Annual level payment on a fully amortising loan."""
    if principal.minor_units <= 0:
        return Money.zero(principal.currency)
    r = annual_rate
    n = Decimal(years)
    if r == 0:
        return principal.scale(Decimal(1) / n)
    factor = r / (1 - (1 + r) ** (-n))
    return principal.scale(factor)


def cca_claim_limited_by_rental_income(net_before_cca: Money, cca_available: Money) -> Money:
    if net_before_cca.minor_units <= 0:
        return Money.zero(net_before_cca.currency)
    return min(net_before_cca, cca_available)


def section_216_withholding(gross_annual: Money, net_annual: Money, *, nr6_approved: bool) -> Money:
    base = net_annual if nr6_approved else gross_annual
    return max(base, Money.zero(base.currency)).scale(Decimal("0.25"))


@dataclass(frozen=True)
class RentalProForma:
    units: int
    gross_potential_rent: Money
    vacancy: Money
    effective_gross_income: Money
    operating_expenses: Money
    management_fee: Money
    net_operating_income: Money
    debt: Money
    annual_debt_service: Money
    interest_year_one: Money
    dscr: Decimal | None
    cap_rate: Decimal | None
    cash_flow_before_tax: Money
    coverage_target: Decimal
    max_supportable_debt: Money
    debt_over_supportable: Money
    building_cost: Money
    cca_rate: Decimal
    cca_available_year_one: Money
    net_rental_before_cca: Money
    cca_claim: Money
    taxable_net_rental: Money
    section_216_tax_split: Money
    withholding_without_nr6: Money
    withholding_with_nr6: Money
    notes: tuple[str, ...]
    sources: tuple[str, ...] = SOURCES


def rental_pro_forma(facts: EngagementFacts, *, cca_rate: Decimal = CLASS_1_RATE, permanent_rate: Decimal | None = None) -> RentalProForma:
    cur = facts.currency
    units = facts.total_units
    rent = facts.expected_monthly_rent_per_unit or Money.zero(cur)
    gpr = rent.scale(12 * units)
    vacancy = gpr.scale(VACANCY)
    egi = gpr - vacancy
    opex = egi.scale(facts.expected_operating_expense_ratio)
    mgmt = egi.scale(facts.property_manager_fee_ratio)
    noi = egi - opex - mgmt
    debt = facts.total_facility_limit
    rate = permanent_rate or facts.permanent_mortgage_rate
    ads = level_payment(debt, rate, PERMANENT_AMORTISATION_YEARS)
    one_dollar_payment = level_payment(Money.from_decimal("1000000.00", cur), rate, PERMANENT_AMORTISATION_YEARS)
    max_debt = (noi.scale(Decimal(1) / LENDER_COVERAGE_TARGET).scale(Decimal(1_000_000_00) / Decimal(one_dollar_payment.minor_units))
                if one_dollar_payment.minor_units else Money.zero(cur))
    over = max(debt - max_debt, Money.zero(cur))
    interest_y1 = debt.scale(rate)
    dscr = (Decimal(noi.minor_units) / Decimal(ads.minor_units)) if ads.minor_units else None
    cap = (Decimal(noi.minor_units) / Decimal(facts.appraised_completed_value.minor_units)) if facts.appraised_completed_value and facts.appraised_completed_value.minor_units else None
    cfbt = noi - ads
    budget = facts.budget_mid or Money.zero(cur)
    building_cost = budget.scale(Decimal("100") / Decimal("113")) if facts.budget_includes_hst and facts.gst_registered else budget
    cca_available = building_cost.scale(cca_rate).scale(Decimal("0.5"))  # half-year rule in the first year
    net_before_cca = noi - interest_y1
    cca_claim = cca_claim_limited_by_rental_income(net_before_cca, cca_available)
    taxable = net_before_cca - cca_claim
    half = taxable.scale(Decimal("0.5"))
    tax_split = section_216_tax(half) + section_216_tax(half)
    notes = (
        f"Rent {rent.format()} a month per unit across {units} units; vacancy {VACANCY:.0%}; operating expenses {facts.expected_operating_expense_ratio:.0%} and management {facts.property_manager_fee_ratio:.0%} of effective gross income.",
        f"Debt service assumes the full {debt.format()} converts to a {PERMANENT_AMORTISATION_YEARS}-year amortising mortgage at {rate:.2%}; the lender's takeout terms replace this.",
        f"At a {LENDER_COVERAGE_TARGET}x coverage requirement the income supports {max_debt.format()} of debt; {over.format()} more would need repaying or refinancing on other terms at takeout.",
        "Building cost for CCA excludes recoverable HST when registered; land is not depreciable; the half-year rule applies in year one.",
        "CCA is limited to net rental income before CCA, so it cannot produce a rental loss.",
        "Tax shown is the s.216 result split equally between two owners; a single owner pays more at graduated rates.",
    )
    return RentalProForma(units, gpr, vacancy, egi, opex, mgmt, noi, debt, ads, interest_y1, dscr, cap, cfbt, LENDER_COVERAGE_TARGET, max_debt, over, building_cost, cca_rate,
                          cca_available, net_before_cca, cca_claim, taxable, tax_split,
                          section_216_withholding(egi, taxable, nr6_approved=False), section_216_withholding(egi, taxable, nr6_approved=True), notes)
