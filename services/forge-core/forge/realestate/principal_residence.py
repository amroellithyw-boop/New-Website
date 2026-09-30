"""The principal residence exemption and the change-of-use deemed disposition.

When a former home is turned into a rental development, s.45(1) deems it sold
and reacquired at fair market value. The gain to that date is partly exempt
under the principal residence formula: (years designated + 1) / years owned,
where the +1 applies only if the taxpayer was resident in Canada in the year
of acquisition. Years while non-resident cannot be designated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import Money
from .facts import EngagementFacts

__all__ = ["PREResult", "principal_residence_exemption", "change_of_use"]

CAPITAL_GAINS_INCLUSION = Decimal("0.5")
SOURCES = (
    "ITA s.40(2)(b) principal residence exemption formula",
    "ITA s.45(1) change in use; s.45(2) election to defer",
    "ITA s.54 principal residence definition; Form T2091(IND)",
    "Personal-use property losses are nil: s.40(2)(g)(iii)",
)


@dataclass(frozen=True)
class PREResult:
    proceeds: Money
    adjusted_cost_base: Money
    gain: Money
    years_owned: int
    years_designated: int
    plus_one: bool
    exempt_fraction: Decimal
    exempt_gain: Money
    capital_gain: Money
    taxable_capital_gain: Money
    notes: tuple[str, ...]
    sources: tuple[str, ...] = SOURCES


def principal_residence_exemption(*, proceeds: Money, adjusted_cost_base: Money, acquisition_year: int, disposition_year: int,
                                  designated_years: tuple[int, ...], resident_in_acquisition_year: bool,
                                  notes: tuple[str, ...] = ()) -> PREResult:
    gain = proceeds - adjusted_cost_base
    years_owned = disposition_year - acquisition_year + 1
    designated = len([y for y in designated_years if acquisition_year <= y <= disposition_year])
    plus_one = resident_in_acquisition_year
    numerator = designated + (1 if plus_one else 0)
    fraction = min(Decimal(numerator) / Decimal(years_owned), Decimal(1)) if years_owned else Decimal(0)
    if gain.minor_units <= 0:
        cur = gain.currency
        return PREResult(proceeds, adjusted_cost_base, gain, years_owned, designated, plus_one, fraction, Money.zero(cur), Money.zero(cur), Money.zero(cur),
                         notes + ("No gain; a loss on a personal-use property is deemed nil",))
    exempt = gain.scale(fraction)
    capital_gain = gain - exempt
    return PREResult(proceeds, adjusted_cost_base, gain, years_owned, designated, plus_one, fraction, exempt, capital_gain,
                     capital_gain.scale(CAPITAL_GAINS_INCLUSION), notes)


def change_of_use(facts: EngagementFacts) -> PREResult | None:
    """The deemed disposition when the former home became a rental development."""
    if not (facts.purchase_price and facts.purchase_date and facts.change_of_use_date):
        return None
    fmv = facts.land_fmv_at_change_of_use
    notes: list[str] = []
    if fmv is None:
        notes.append("Fair market value at the change-of-use date is not supplied; an appraisal of the land as at that date is required. The purchase price is used as a placeholder, which produces no gain.")
        fmv = facts.purchase_price + facts.acquisition_costs
    if facts.building_demolished:
        notes.append("The house was demolished: the property is one capital property, the demolition does not create a deductible loss on a personal-use property, and the whole adjusted cost base stays with the land.")
    notes.append("Filing: the deemed disposition is reported on a non-resident T1 for the change-of-use year with Form T2091(IND) designating the principal residence years; no s.116 certificate is needed for a deemed disposition.")
    notes.append("Option: a s.45(2) election defers the deemed disposition, but years while non-resident still cannot be designated, so it defers rather than reduces the gain.")
    notes.append("Both co-owners report their share of the gain.")
    return principal_residence_exemption(
        proceeds=fmv, adjusted_cost_base=facts.purchase_price + facts.acquisition_costs,
        acquisition_year=facts.purchase_date.year, disposition_year=facts.change_of_use_date.year,
        designated_years=facts.principal_residence_years, resident_in_acquisition_year=facts.resident_in_acquisition_year,
        notes=tuple(notes),
    )
