"""GST/HST on a new residential rental build: self-supply, rebates, timing.

The rules applied, each recorded on the result so a reviewer can check them:

- Self-supply (ETA s.191): a builder who gives possession of a newly built
  residential complex under a lease is deemed to have sold and repurchased it
  at fair market value at the later of substantial completion and first
  occupancy, and must account for HST on that value.
- New residential rental property rebate (ETA s.256.2): 36% of the federal
  part, at most $6,300 per qualifying unit, full where the unit's FMV is at
  most $350,000 and phased out to nil at $450,000. Ontario rebate: 75% of the
  provincial part, at most $24,000 per unit, no phase-out.
- Purpose-built rental housing enhancement (2023): 100% of the federal part
  where construction began after 13 September 2023 and before 2031, the
  building is substantially complete before 2036, it has at least four
  private apartment units, and at least 90% of units are held for long-term
  rental. Ontario mirrored this for the provincial part.
- Input tax credits: a registered builder recovers the HST on construction
  inputs as they are incurred.

The numbers here are arithmetic on the client's own facts. Whether a
detached garden suite is a separate residential complex, and whether both
co-owners are builders for these purposes, are questions the plan flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..money import Money
from ..tax.calendar import Deadline
from .facts import Complex, EngagementFacts

__all__ = [
    "FEDERAL_PART", "PROVINCIAL_PART_ON", "Eligibility", "UnitRebate", "RebateResult", "HstPosition",
    "federal_nrrp_rebate", "ontario_nrrp_rebate", "pbrh_eligibility", "self_supply_tax", "rebate_for_complex",
    "hst_position",
]

FEDERAL_PART = Decimal("0.05")
PROVINCIAL_PART_ON = Decimal("0.08")
NRRP_FEDERAL_RATE = Decimal("0.36")
NRRP_FEDERAL_MAX = "6300.00"
NRRP_FEDERAL_FULL_TO = "350000.00"
NRRP_FEDERAL_NIL_FROM = "450000.00"
NRRP_ONTARIO_RATE = Decimal("0.75")
NRRP_ONTARIO_MAX = "24000.00"
PBRH_START_AFTER = date(2023, 9, 13)
PBRH_START_BEFORE = date(2031, 1, 1)
PBRH_COMPLETE_BEFORE = date(2036, 1, 1)
PBRH_MIN_UNITS = 4
PBRH_MIN_LONG_TERM_SHARE = Decimal("0.9")
REBATE_APPLICATION_YEARS = 2

SOURCES = (
    "ETA s.191 self-supply of residential complexes",
    "ETA s.256.2 new residential rental property rebate; CRA guide RC4231",
    "Enhanced GST rental rebate for purpose-built rental housing, announced 14 September 2023",
    "Ontario enhanced HST new residential rental property rebate, announced 1 November 2023",
    "Forms GST524, GST525 and RC7524-ON",
)


def federal_nrrp_rebate(unit_fmv: Money) -> Money:
    """36% of the federal part, capped, phased out between $350,000 and $450,000."""
    cur = unit_fmv.currency
    gst = unit_fmv.scale(FEDERAL_PART)
    base = min(gst.scale(NRRP_FEDERAL_RATE), Money.from_decimal(NRRP_FEDERAL_MAX, cur))
    full_to = Money.from_decimal(NRRP_FEDERAL_FULL_TO, cur)
    nil_from = Money.from_decimal(NRRP_FEDERAL_NIL_FROM, cur)
    if unit_fmv <= full_to:
        return base
    if unit_fmv >= nil_from:
        return Money.zero(cur)
    remaining = Decimal((nil_from - unit_fmv).minor_units) / Decimal((nil_from - full_to).minor_units)
    return base.scale(remaining)


def ontario_nrrp_rebate(unit_fmv: Money) -> Money:
    """75% of the provincial part, at most $24,000, no phase-out."""
    return min(unit_fmv.scale(PROVINCIAL_PART_ON).scale(NRRP_ONTARIO_RATE), Money.from_decimal(NRRP_ONTARIO_MAX, unit_fmv.currency))


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: tuple[str, ...]
    confirm: tuple[str, ...] = ()


def pbrh_eligibility(cx: Complex, *, construction_start: date | None, completion: date | None) -> Eligibility:
    reasons: list[str] = []
    confirm: list[str] = []
    ok = True
    if cx.units < PBRH_MIN_UNITS:
        ok = False
        reasons.append(f"{cx.units} unit(s); at least {PBRH_MIN_UNITS} private apartment units are required")
    else:
        reasons.append(f"{cx.units} units meets the four-unit minimum")
    if not cx.private_kitchen_bath_living:
        ok = False
        reasons.append("units must each have a private kitchen, bathroom and living area")
    long_term = Decimal(1) if cx.intended_use == "long_term_rental" else Decimal(0)
    if long_term < PBRH_MIN_LONG_TERM_SHARE:
        ok = False
        reasons.append("at least 90% of units must be held for long-term rental")
    else:
        reasons.append("all units intended for long-term rental")
    if construction_start is None:
        confirm.append("construction start date not supplied")
    elif not (PBRH_START_AFTER < construction_start < PBRH_START_BEFORE):
        ok = False
        reasons.append(f"construction began {construction_start}; it must begin after {PBRH_START_AFTER} and before {PBRH_START_BEFORE}")
    else:
        reasons.append(f"construction began {construction_start}, inside the window")
    if completion is None:
        confirm.append("completion date not supplied")
    elif completion >= PBRH_COMPLETE_BEFORE:
        ok = False
        reasons.append(f"substantial completion {completion} is not before {PBRH_COMPLETE_BEFORE}")
    if cx.detached and cx.units < PBRH_MIN_UNITS:
        confirm.append("a detached building is normally its own residential complex; it cannot borrow the main building's unit count")
    confirm.append("what counts as the start of construction (excavation is generally accepted) should be documented with the permit and site records")
    return Eligibility(ok, tuple(reasons), tuple(confirm))


def self_supply_tax(fmv: Money) -> tuple[Money, Money, Money]:
    """(federal part, provincial part, total) HST on a deemed self-supply at FMV."""
    fed = fmv.scale(FEDERAL_PART)
    prov = fmv.scale(PROVINCIAL_PART_ON)
    return fed, prov, fed + prov


@dataclass(frozen=True)
class UnitRebate:
    unit: int
    fmv: Money
    federal: Money
    ontario: Money


@dataclass(frozen=True)
class RebateResult:
    complex_name: str
    units: int
    fmv: Money
    federal_tax: Money
    provincial_tax: Money
    total_tax: Money
    pbrh: Eligibility
    per_unit: tuple[UnitRebate, ...]
    federal_rebate: Money
    ontario_rebate: Money
    basis: str
    confirm: tuple[str, ...] = ()

    @property
    def net_payable(self) -> Money:
        return self.total_tax - self.federal_rebate - self.ontario_rebate

    @property
    def total_rebate(self) -> Money:
        return self.federal_rebate + self.ontario_rebate


def rebate_for_complex(cx: Complex, *, construction_start: date | None, completion: date | None,
                       fmv: Money | None = None) -> RebateResult:
    value = fmv or cx.expected_fmv
    if value is None:
        raise ValueError(f"{cx.name}: an expected fair market value is required")
    fed, prov, total = self_supply_tax(value)
    pbrh = pbrh_eligibility(cx, construction_start=construction_start, completion=completion)
    shares = cx.unit_shares
    unit_values = value.allocate(shares)
    per_unit = tuple(UnitRebate(i + 1, uv, federal_nrrp_rebate(uv), ontario_nrrp_rebate(uv)) for i, uv in enumerate(unit_values))
    confirm = list(pbrh.confirm)
    if pbrh.eligible:
        basis = "purpose-built rental housing: 100% of the federal part and, in Ontario, 100% of the provincial part"
        federal_rebate, ontario_rebate = fed, prov
        confirm.append("the enhanced rebate is claimed on the NRRP application; confirm the current form version and the Ontario schedule")
    else:
        basis = "standard new residential rental property rebate, computed per unit on floor-area share of FMV"
        federal_rebate = Money.zero(value.currency)
        ontario_rebate = Money.zero(value.currency)
        for u in per_unit:
            federal_rebate = federal_rebate + u.federal
            ontario_rebate = ontario_rebate + u.ontario
    if not cx.floor_area_share and cx.units > 1:
        confirm.append("units assumed equal in floor area; the rebate uses each unit's actual share")
    confirm.append("FMV at the self-supply date is what counts; an appraisal dated near completion is required")
    return RebateResult(cx.name, cx.units, value, fed, prov, total, pbrh, per_unit, federal_rebate, ontario_rebate, basis, tuple(confirm))


@dataclass
class HstPosition:
    results: tuple[RebateResult, ...]
    itc_estimate: Money
    itc_basis: str
    self_supply_date: date | None
    deadlines: list[Deadline] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: tuple[str, ...] = SOURCES

    @property
    def total_self_supply_tax(self) -> Money:
        t = Money.zero()
        for r in self.results:
            t = t + r.total_tax
        return t

    @property
    def total_rebates(self) -> Money:
        t = Money.zero()
        for r in self.results:
            t = t + r.total_rebate
        return t

    @property
    def net_payable_at_completion(self) -> Money:
        return self.total_self_supply_tax - self.total_rebates

    @property
    def net_cash_effect_over_project(self) -> Money:
        """ITCs recovered during construction less net tax at completion. Positive is money in."""
        return self.itc_estimate - self.net_payable_at_completion


def _end_of_month(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def hst_position(facts: EngagementFacts, *, today: date) -> HstPosition:
    results = tuple(rebate_for_complex(cx, construction_start=facts.construction_start, completion=facts.expected_completion)
                    for cx in facts.complexes if cx.expected_fmv is not None)
    cur = facts.currency
    budget = facts.budget_mid or Money.zero(cur)
    if facts.budget_includes_hst:
        itc = budget.scale(Decimal("13") / Decimal("113"))
        basis = "budget stated HST-inclusive; HST content taken as 13/113 of the midpoint budget"
    else:
        itc = budget.scale(Decimal("0.13"))
        basis = "budget stated before HST; 13% added"
    warnings: list[str] = []
    if not facts.gst_registered:
        warnings.append("Not registered for GST/HST: no input tax credits can be claimed until registration, and the self-supply still applies.")
    else:
        if not facts.construction_itcs_claimed and facts.spent_to_date and facts.spent_to_date.minor_units > 0:
            spent_hst = facts.spent_to_date.scale(Decimal("13") / Decimal("113")) if facts.budget_includes_hst else facts.spent_to_date.scale(Decimal("0.13"))
            warnings.append(f"About {spent_hst.format()} of HST on costs to date has not been claimed as input tax credits; claim it on the next return.")
        if facts.owned_personally_jointly and not facts.co_owner_registered:
            warnings.append("The property is co-owned but only one owner holds the GST/HST account. Each co-owner is a builder of their interest; "
                            "confirm whether both register or the co-ownership registers as a partnership before claiming credits or self-assessing.")
        if facts.gst_returns_outstanding:
            warnings.append(f"GST/HST return(s) outstanding for {', '.join(facts.gst_returns_outstanding)}; file before claiming construction credits.")
    if facts.invoices_issued_to and "personally" in facts.invoices_issued_to and facts.owned_personally_jointly:
        warnings.append("Construction invoices are issued to one owner only; credits follow the recipient named on the invoice. Align invoicing with the registration decision.")
    single = [cx for cx in facts.complexes if cx.units < PBRH_MIN_UNITS]
    if single:
        warnings.append(f"{', '.join(c.name for c in single)}: below the four-unit minimum for the enhanced rebate; the standard per-unit rebate applies, "
                        "which is nil federally above $450,000 of unit value.")
    self_supply_on = facts.expected_completion
    deadlines: list[Deadline] = []
    if self_supply_on:
        eom = _end_of_month(self_supply_on)
        return_due = _end_of_month(date(eom.year + (eom.month == 12), eom.month % 12 + 1, 1))
        deadlines.append(Deadline("hst_self_supply", self_supply_on, "Self-supply: HST on fair market value becomes payable at the later of substantial completion and first tenant possession", "CRA", confirm=True))
        deadlines.append(Deadline("hst_return_self_supply", return_due, "GST/HST return reporting the self-supply (assumes a monthly filer; quarterly or annual changes the date)", "CRA", confirm=True))
        rebate_due = date(eom.year + REBATE_APPLICATION_YEARS, eom.month, eom.day)
        deadlines.append(Deadline("nrrp_rebate_application", rebate_due, "Last day to apply for the new residential rental property rebate (two years after the end of the month the tax became payable)", "CRA"))
        deadlines.append(Deadline("appraisal", self_supply_on - timedelta(days=30), "Appraisal of fair market value at completion, per building, with floor areas per unit", "Appraiser"))
    for yr in facts.gst_returns_outstanding:
        deadlines.append(Deadline("gst_return_outstanding", today, f"Outstanding GST/HST return for {yr}: file now (nil if the records support it)", "CRA"))
    return HstPosition(results, itc, basis, self_supply_on, deadlines, warnings)
