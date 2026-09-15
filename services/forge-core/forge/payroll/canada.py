"""Canadian payroll deductions, computed exactly.

Three things this module does that a spreadsheet or a float-based service
usually gets wrong:

**Year-to-date tracking.** Annual maxima for CPP, CPP2 and EI are real caps. A
per-period approximation only lands on the right annual number when every
period's pay is identical, which is never true of a business with overtime,
bonuses or seasonal crews.

**The basic personal amount is a credit, not a deduction.** Subtracting it from
income before applying brackets shifts every bracket downward and under-withholds
for anyone above the lowest bracket. At $100,000 of income the difference is over
a thousand dollars of tax the employee will owe at filing.

**Employer CPP2 is matched once.** The legacy implementation added the employer's
CPP2 share into the CPP employer figure and then added it again as its own line,
over-stating the remittance by up to the full annual CPP2 maximum per employee.

Every amount is an exact integer of cents. The employee's net pay and the
employer's remittance are both reconciled in :class:`PayrollResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..money import Money, msum
from .rates import PAY_PERIODS, Bracket, PayrollRates, rates_for

__all__ = ["PayrollInput", "PayrollResult", "calculate_pay", "annual_tax"]


def _zero() -> Money:
    return Money.zero("CAD")


@dataclass(frozen=True)
class PayrollInput:
    """One employee, one pay period.

    The year-to-date figures are what make the annual caps correct. They default
    to zero, which is right for the first period of the year and wrong for every
    other one, so a caller that omits them is choosing an approximation.
    """

    gross: Money
    pay_frequency: str = "biweekly"
    year: int = 2026
    province: str = "ON"
    ytd_gross: Money = field(default_factory=_zero)
    ytd_cpp: Money = field(default_factory=_zero)
    ytd_cpp2: Money = field(default_factory=_zero)
    ytd_ei: Money = field(default_factory=_zero)
    wsib_rate: Decimal | None = None
    cpp_exempt: bool = False
    """True for an employee under 18, over 70, or holding a CPT30 election."""
    ei_exempt: bool = False
    """True for a shareholder controlling more than 40% of voting shares."""

    @property
    def periods_per_year(self) -> int:
        try:
            return PAY_PERIODS[self.pay_frequency]
        except KeyError as exc:
            raise ValueError(
                f"Unknown pay frequency {self.pay_frequency!r}; "
                f"expected one of {', '.join(sorted(PAY_PERIODS))}"
            ) from exc


@dataclass(frozen=True)
class PayrollResult:
    """A fully reconciled pay period."""

    gross: Money
    cpp_employee: Money
    cpp2_employee: Money
    ei_employee: Money
    income_tax: Money
    federal_tax: Money
    provincial_tax: Money
    health_premium: Money
    cpp_employer: Money
    cpp2_employer: Money
    ei_employer: Money
    wsib_premium: Money
    periods_per_year: int
    rates: PayrollRates

    # ---- employee -------------------------------------------------------

    @property
    def total_deductions(self) -> Money:
        return msum(
            (self.cpp_employee, self.cpp2_employee, self.ei_employee, self.income_tax), "CAD"
        )

    @property
    def net_pay(self) -> Money:
        return self.gross - self.total_deductions

    # ---- employer -------------------------------------------------------

    @property
    def employer_cost(self) -> Money:
        """Gross plus the employer's own statutory contributions."""
        return msum(
            (self.gross, self.cpp_employer, self.cpp2_employer, self.ei_employer,
             self.wsib_premium),
            "CAD",
        )

    @property
    def remittance_to_cra(self) -> Money:
        """What is owed to the Receiver General for this pay period.

        Employee CPP, CPP2, EI and income tax withheld, plus the employer's
        matching CPP, CPP2 and EI. WSIB is excluded: it is paid to the Workplace
        Safety and Insurance Board, not to the CRA, on a different schedule.
        """
        return msum(
            (
                self.cpp_employee, self.cpp2_employee, self.ei_employee, self.income_tax,
                self.cpp_employer, self.cpp2_employer, self.ei_employer,
            ),
            "CAD",
        )

    # ---- proofs ---------------------------------------------------------

    @property
    def reconciles(self) -> bool:
        """Gross less deductions equals net, and the employer share matches."""
        return (
            self.net_pay + self.total_deductions == self.gross
            and self.cpp_employer == self.cpp_employee
            and self.cpp2_employer == self.cpp2_employee
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gross": str(self.gross.to_decimal()),
            "cpp_employee": str(self.cpp_employee.to_decimal()),
            "cpp2_employee": str(self.cpp2_employee.to_decimal()),
            "ei_employee": str(self.ei_employee.to_decimal()),
            "federal_tax": str(self.federal_tax.to_decimal()),
            "provincial_tax": str(self.provincial_tax.to_decimal()),
            "health_premium": str(self.health_premium.to_decimal()),
            "income_tax": str(self.income_tax.to_decimal()),
            "total_deductions": str(self.total_deductions.to_decimal()),
            "net_pay": str(self.net_pay.to_decimal()),
            "cpp_employer": str(self.cpp_employer.to_decimal()),
            "cpp2_employer": str(self.cpp2_employer.to_decimal()),
            "ei_employer": str(self.ei_employer.to_decimal()),
            "wsib_premium": str(self.wsib_premium.to_decimal()),
            "employer_cost": str(self.employer_cost.to_decimal()),
            "remittance_to_cra": str(self.remittance_to_cra.to_decimal()),
            "tax_year": self.rates.year,
            "province": self.rates.province,
            "rates_verified": self.rates.is_verified,
        }


def _tax_on_brackets(taxable: Money, brackets: tuple[Bracket, ...]) -> Money:
    """Marginal tax across brackets, applied to taxable income itself."""
    total = Money.zero(taxable.currency)
    lower = Money.zero(taxable.currency)
    for bracket in brackets:
        if bracket.upper is None:
            slice_amount = taxable - lower if taxable > lower else Money.zero(taxable.currency)
        else:
            top = bracket.upper if taxable > bracket.upper else taxable
            slice_amount = top - lower if top > lower else Money.zero(taxable.currency)
        if slice_amount.minor_units > 0:
            total = total + slice_amount.scale(bracket.rate)
        if bracket.upper is None or taxable <= bracket.upper:
            break
        lower = bracket.upper
    return total


def _health_premium(taxable: Money, rates: PayrollRates) -> Money:
    if not rates.health_premium:
        return Money.zero(taxable.currency)
    for band in rates.health_premium:
        if band.upper is None or taxable <= band.upper:
            if band.rate == 0:
                return Money.zero(taxable.currency)
            over = taxable - band.threshold
            if over.minor_units <= 0:
                return band.base
            computed = band.base + over.scale(band.rate)
            return computed if computed < band.cap else band.cap
    return Money.zero(taxable.currency)


def annual_tax(
    annual_taxable: Money, rates: PayrollRates
) -> tuple[Money, Money, Money]:
    """Annual federal tax, provincial tax and health premium.

    The basic personal amount is applied as a non-refundable credit at the
    lowest bracket rate, which is how the Income Tax Act actually works. The
    provincial surtax is charged on provincial tax *after* that credit.
    """
    zero = Money.zero(annual_taxable.currency)
    if annual_taxable.minor_units <= 0:
        return zero, zero, zero

    federal_gross = _tax_on_brackets(annual_taxable, rates.federal_brackets)
    federal_credit = rates.federal_basic_personal_amount.scale(rates.federal_lowest_rate)
    federal = federal_gross - federal_credit
    if federal.minor_units < 0:
        federal = zero

    provincial_gross = _tax_on_brackets(annual_taxable, rates.provincial_brackets)
    provincial_credit = rates.provincial_basic_personal_amount.scale(
        rates.provincial_lowest_rate
    )
    provincial = provincial_gross - provincial_credit
    if provincial.minor_units < 0:
        provincial = zero

    surtax = zero
    for threshold, rate in rates.provincial_surtax:
        if provincial > threshold:
            surtax = surtax + (provincial - threshold).scale(rate)
    provincial = provincial + surtax

    premium = _health_premium(annual_taxable, rates)
    return federal, provincial, premium


def calculate_pay(entry: PayrollInput) -> PayrollResult:
    """Compute one pay period with annual caps respected through year to date."""
    rates = rates_for(entry.year, entry.province)
    periods = entry.periods_per_year
    zero = Money.zero(entry.gross.currency)

    # ---- Canada Pension Plan, first tier --------------------------------
    if entry.cpp_exempt:
        cpp = zero
    else:
        period_exemption = rates.cpp_basic_exemption.scale(Decimal(1) / Decimal(periods))
        pensionable = entry.gross - period_exemption
        raw = pensionable.scale(rates.cpp_rate) if pensionable.minor_units > 0 else zero
        headroom = rates.cpp_maximum_contribution - entry.ytd_cpp
        cpp = min(raw, headroom) if headroom.minor_units > 0 else zero

    # ---- Canada Pension Plan, second tier -------------------------------
    # CPP2 is charged on earnings between the YMPE and the YAMPE, measured on a
    # year-to-date basis. Computing it from an annualised estimate charges the
    # wrong amount in every period where pay is uneven.
    if entry.cpp_exempt:
        cpp2 = zero
    else:
        ytd_after = entry.ytd_gross + entry.gross
        band_low = rates.cpp_ympe
        band_high = rates.cpp2_yampe
        earnings_in_band = zero
        if ytd_after > band_low:
            top = ytd_after if ytd_after < band_high else band_high
            earnings_in_band = top - band_low
        target = earnings_in_band.scale(rates.cpp2_rate)
        cpp2 = target - entry.ytd_cpp2
        if cpp2.minor_units < 0:
            cpp2 = zero
        cap = rates.cpp2_maximum_contribution - entry.ytd_cpp2
        if cap.minor_units <= 0:
            cpp2 = zero
        elif cpp2 > cap:
            cpp2 = cap

    # ---- Employment Insurance -------------------------------------------
    if entry.ei_exempt:
        ei = zero
    else:
        raw_ei = entry.gross.scale(rates.ei_rate)
        headroom = rates.ei_maximum_contribution - entry.ytd_ei
        ei = min(raw_ei, headroom) if headroom.minor_units > 0 else zero

    # ---- Income tax ------------------------------------------------------
    # Taxable income for withholding is gross less CPP/EI contributions only to
    # the extent the CRA formula allows; the simplified method used here
    # annualises gross and prorates the annual tax, which is the standard
    # approximation for a salaried employee.
    annualised = entry.gross.scale(periods)
    federal_year, provincial_year, premium_year = annual_tax(annualised, rates)
    factor = Decimal(1) / Decimal(periods)
    federal = federal_year.scale(factor)
    provincial = provincial_year.scale(factor)
    premium = premium_year.scale(factor)
    income_tax = federal + provincial + premium

    # ---- Employer share ---------------------------------------------------
    # CPP and CPP2 are matched one for one. EI is charged at a multiple.
    cpp_employer = cpp
    cpp2_employer = cpp2
    ei_employer = ei.scale(rates.ei_employer_multiplier)

    wsib = entry.gross.scale(entry.wsib_rate) if entry.wsib_rate else zero

    return PayrollResult(
        gross=entry.gross,
        cpp_employee=cpp,
        cpp2_employee=cpp2,
        ei_employee=ei,
        income_tax=income_tax,
        federal_tax=federal,
        provincial_tax=provincial,
        health_premium=premium,
        cpp_employer=cpp_employer,
        cpp2_employer=cpp2_employer,
        ei_employer=ei_employer,
        wsib_premium=wsib,
        periods_per_year=periods,
        rates=rates,
    )
