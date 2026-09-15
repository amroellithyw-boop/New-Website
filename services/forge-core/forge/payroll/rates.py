"""Statutory payroll constants, versioned by tax year.

IMPORTANT. These constants are carried over from a working practice's system and
are believed correct for the year stated, but they are *data*, not truth. Before
any payroll is run on them for a real employer they must be checked against the
Canada Revenue Agency's T4127 payroll formulas and the T4032 deduction tables
for the province, and against the current EI premium rate published by the
Canada Employment Insurance Commission.

The structure is deliberately separated from the numbers so that an annual
update is a data change with a test, not a code change. ``verified_against``
records what was actually checked and when; an unverified year is reported
rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import Money

__all__ = [
    "Bracket",
    "PayrollRates",
    "ONTARIO_HEALTH_PREMIUM",
    "RATES",
    "rates_for",
    "PAY_PERIODS",
]

# Pay frequency to periods per year.
PAY_PERIODS: dict[str, int] = {
    "weekly": 52,
    "biweekly": 26,
    "semi-monthly": 24,
    "monthly": 12,
    "quarterly": 4,
}


@dataclass(frozen=True)
class Bracket:
    """One marginal tax bracket. ``upper`` of ``None`` means the top bracket."""

    upper: Money | None
    rate: Decimal


@dataclass(frozen=True)
class OntarioHealthPremiumBand:
    upper: Money | None
    base: Money
    rate: Decimal
    threshold: Money
    cap: Money


def _m(value: str) -> Money:
    return Money.from_decimal(value, "CAD")


# The Ontario Health Premium table. Stable for many years, but still a table
# that has to be checked rather than assumed.
ONTARIO_HEALTH_PREMIUM: tuple[OntarioHealthPremiumBand, ...] = (
    OntarioHealthPremiumBand(_m("20000.00"), _m("0.00"), Decimal("0"), _m("0.00"), _m("0.00")),
    OntarioHealthPremiumBand(_m("36000.00"), _m("0.00"), Decimal("0.06"), _m("20000.00"), _m("300.00")),
    OntarioHealthPremiumBand(_m("48000.00"), _m("300.00"), Decimal("0.06"), _m("36000.00"), _m("450.00")),
    OntarioHealthPremiumBand(_m("72000.00"), _m("450.00"), Decimal("0.25"), _m("48000.00"), _m("600.00")),
    OntarioHealthPremiumBand(_m("200000.00"), _m("600.00"), Decimal("0.25"), _m("72000.00"), _m("750.00")),
    OntarioHealthPremiumBand(None, _m("750.00"), Decimal("0.25"), _m("200000.00"), _m("900.00")),
)


@dataclass(frozen=True)
class PayrollRates:
    """Every statutory number needed to compute one pay period."""

    year: int
    province: str

    # Canada Pension Plan, first tier.
    cpp_rate: Decimal
    cpp_ympe: Money
    """Year's maximum pensionable earnings."""
    cpp_basic_exemption: Money

    # Canada Pension Plan, second tier (CPP2).
    cpp2_rate: Decimal
    cpp2_yampe: Money
    """Year's additional maximum pensionable earnings. CPP2 applies between
    the YMPE and this figure."""

    # Employment Insurance.
    ei_rate: Decimal
    ei_maximum_insurable: Money
    ei_employer_multiplier: Decimal

    # Income tax.
    federal_basic_personal_amount: Money
    federal_brackets: tuple[Bracket, ...]
    provincial_basic_personal_amount: Money
    provincial_brackets: tuple[Bracket, ...]
    provincial_surtax: tuple[tuple[Money, Decimal], ...] = ()
    """(threshold, rate) pairs applied cumulatively to provincial tax after
    credits. Ontario charges 20% above one threshold and a further 36% above a
    second, and both apply at once in the upper band."""
    health_premium: tuple[OntarioHealthPremiumBand, ...] = ()

    verified_against: str = ""
    """What was checked, and when. Empty means nobody has verified this year."""

    @property
    def is_verified(self) -> bool:
        return bool(self.verified_against)

    @property
    def cpp_maximum_contribution(self) -> Money:
        """Annual employee CPP1 maximum: (YMPE - exemption) x rate."""
        return (self.cpp_ympe - self.cpp_basic_exemption).scale(self.cpp_rate)

    @property
    def cpp2_maximum_contribution(self) -> Money:
        """Annual employee CPP2 maximum: (YAMPE - YMPE) x rate."""
        return (self.cpp2_yampe - self.cpp_ympe).scale(self.cpp2_rate)

    @property
    def ei_maximum_contribution(self) -> Money:
        return self.ei_maximum_insurable.scale(self.ei_rate)

    @property
    def federal_lowest_rate(self) -> Decimal:
        return self.federal_brackets[0].rate

    @property
    def provincial_lowest_rate(self) -> Decimal:
        return self.provincial_brackets[0].rate


ONTARIO_2026 = PayrollRates(
    year=2026,
    province="ON",
    cpp_rate=Decimal("0.0595"),
    cpp_ympe=_m("74600.00"),
    cpp_basic_exemption=_m("3500.00"),
    cpp2_rate=Decimal("0.04"),
    cpp2_yampe=_m("85000.00"),
    ei_rate=Decimal("0.0163"),
    ei_maximum_insurable=_m("68900.00"),
    ei_employer_multiplier=Decimal("1.4"),
    federal_basic_personal_amount=_m("16452.00"),
    federal_brackets=(
        Bracket(_m("58523.00"), Decimal("0.14")),
        Bracket(_m("117045.00"), Decimal("0.205")),
        Bracket(_m("181440.00"), Decimal("0.26")),
        Bracket(_m("258482.00"), Decimal("0.29")),
        Bracket(None, Decimal("0.33")),
    ),
    provincial_basic_personal_amount=_m("12989.00"),
    provincial_brackets=(
        Bracket(_m("53891.00"), Decimal("0.0505")),
        Bracket(_m("107785.00"), Decimal("0.0915")),
        Bracket(_m("150000.00"), Decimal("0.1116")),
        Bracket(_m("220000.00"), Decimal("0.1216")),
        Bracket(None, Decimal("0.1316")),
    ),
    provincial_surtax=(
        (_m("5818.00"), Decimal("0.20")),
        (_m("7446.00"), Decimal("0.36")),
    ),
    health_premium=ONTARIO_HEALTH_PREMIUM,
    verified_against="",  # nobody has checked this against CRA T4127 yet
)


RATES: dict[tuple[int, str], PayrollRates] = {
    (2026, "ON"): ONTARIO_2026,
}


def rates_for(year: int, province: str = "ON") -> PayrollRates:
    """Look up statutory rates, failing loudly for an unsupported year."""
    key = (year, province.upper())
    if key not in RATES:
        available = ", ".join(f"{y} {p}" for y, p in sorted(RATES))
        raise KeyError(
            f"No payroll rates loaded for {year} {province.upper()}. "
            f"Available: {available}. Add them to forge.payroll.rates with a test "
            "rather than approximating from an adjacent year."
        )
    return RATES[key]
