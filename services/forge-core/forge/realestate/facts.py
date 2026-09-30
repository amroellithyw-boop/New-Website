"""The facts of one engagement, as the client and the engagement letter state them.

Facts are data, not conclusions. Every figure here is what the client told
us or what a document says; the modules that consume it derive the rest and
say what they assumed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..money import Money

__all__ = ["Complex", "Facility", "Phase", "EngagementFacts", "load_facts", "EXAMPLE_FACTS"]


def _m(v: Any, cur: str = "CAD") -> Money | None:
    if v is None or v == "":
        return None
    return Money.from_decimal(str(v), cur)


def _d(v: Any) -> date | None:
    return date.fromisoformat(v) if v else None


@dataclass(frozen=True)
class Complex:
    """One residential complex: a building with one or more units."""

    name: str
    units: int
    kind: str  # "multiple_unit" | "single_unit"
    detached: bool = False
    intended_use: str = "long_term_rental"  # long_term_rental | short_term_rental | owner_occupied | sale
    floor_area_share: tuple[Decimal, ...] = ()
    """Each unit's share of the complex's floor area, summing to 1. Equal
    shares are assumed when empty."""
    expected_fmv: Money | None = None
    """Appraised or expected fair market value of this complex at completion."""
    private_kitchen_bath_living: bool = True

    @property
    def unit_shares(self) -> tuple[Decimal, ...]:
        if self.floor_area_share:
            return self.floor_area_share
        return tuple(Decimal(1) / Decimal(self.units) for _ in range(self.units))


@dataclass(frozen=True)
class Facility:
    name: str
    limit: Money
    drawn: Money
    annual_rate: Decimal
    kind: str = "construction_loan"
    interest_capitalised: bool = True


@dataclass(frozen=True)
class Phase:
    number: int
    title: str
    fee: Money
    cadence: str  # "fixed" | "monthly"
    authorised: bool
    deliverables: tuple[str, ...]


@dataclass
class EngagementFacts:
    engagement_id: str
    client_name: str
    co_owner_name: str = ""
    property_label: str = ""
    province: str = "ON"
    municipality: str = "Toronto"
    currency: str = "CAD"

    # Residency.
    client_citizenship: str = "unknown"  # canadian | permanent_resident | foreign_national | unknown
    resident_country: str = "Italy"
    departure_date: date | None = None
    last_t1_year: int | None = None
    departure_reported_on_t1: bool = False
    later_t1_returns_filed: bool = False
    residency_determination_done: bool = False
    property_value_at_departure: Money | None = None

    # GST/HST account.
    gst_registered: bool = False
    gst_account_holder: str = ""
    gst_returns_outstanding: tuple[str, ...] = ()
    gst_nil_returns_filed: tuple[str, ...] = ()
    construction_itcs_claimed: bool = False
    co_owner_registered: bool = False

    # Property.
    purchase_date: date | None = None
    purchase_price: Money | None = None
    acquisition_costs: Money = field(default_factory=lambda: Money.zero())
    former_principal_residence: bool = False
    principal_residence_years: tuple[int, ...] = ()
    resident_in_acquisition_year: bool = True
    building_demolished: bool = False
    change_of_use_date: date | None = None
    land_fmv_at_change_of_use: Money | None = None
    owned_personally_jointly: bool = True

    # Project.
    construction_start: date | None = None
    expected_completion: date | None = None
    budget_low: Money | None = None
    budget_high: Money | None = None
    budget_includes_hst: bool = True
    spent_to_date: Money | None = None
    spent_as_of: date | None = None
    appraised_completed_value: Money | None = None
    complexes: tuple[Complex, ...] = ()
    facilities: tuple[Facility, ...] = ()
    contracts_directly_with_trades: bool = True
    invoices_issued_to: str = "client personally"

    # Operations (Phase 4 projections).
    expected_monthly_rent_per_unit: Money | None = None
    expected_operating_expense_ratio: Decimal = Decimal("0.35")
    property_manager_fee_ratio: Decimal = Decimal("0.05")
    permanent_mortgage_rate: Decimal = Decimal("0.05")
    """Expected takeout mortgage rate at completion; construction rates are higher."""

    # Engagement.
    phases: tuple[Phase, ...] = ()
    options_under_consideration: tuple[str, ...] = ()
    hourly_rate: Money | None = None

    @property
    def total_units(self) -> int:
        return sum(c.units for c in self.complexes)

    @property
    def budget_mid(self) -> Money | None:
        if self.budget_low and self.budget_high:
            return (self.budget_low + self.budget_high).scale(Decimal("0.5"))
        return self.budget_low or self.budget_high

    @property
    def total_facility_limit(self) -> Money:
        total = Money.zero(self.currency)
        for f in self.facilities:
            total = total + f.limit
        return total

    @property
    def total_drawn(self) -> Money:
        total = Money.zero(self.currency)
        for f in self.facilities:
            total = total + f.drawn
        return total

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EngagementFacts:
        cur = raw.get("currency", "CAD")
        complexes = tuple(
            Complex(
                name=c["name"], units=int(c["units"]), kind=c.get("kind", "multiple_unit" if int(c["units"]) > 1 else "single_unit"),
                detached=bool(c.get("detached", False)), intended_use=c.get("intended_use", "long_term_rental"),
                floor_area_share=tuple(Decimal(str(s)) for s in c.get("floor_area_share", ())),
                expected_fmv=_m(c.get("expected_fmv"), cur),
                private_kitchen_bath_living=bool(c.get("private_kitchen_bath_living", True)),
            )
            for c in raw.get("complexes", ())
        )
        facilities = tuple(
            Facility(name=f["name"], limit=_m(f["limit"], cur), drawn=_m(f.get("drawn", "0"), cur),
                     annual_rate=Decimal(str(f.get("annual_rate", "0.08"))), kind=f.get("kind", "construction_loan"),
                     interest_capitalised=bool(f.get("interest_capitalised", True)))
            for f in raw.get("facilities", ())
        )
        phases = tuple(
            Phase(number=int(p["number"]), title=p["title"], fee=_m(p["fee"], cur), cadence=p.get("cadence", "fixed"),
                  authorised=bool(p.get("authorised", False)), deliverables=tuple(p.get("deliverables", ())))
            for p in raw.get("phases", ())
        )
        money_keys = {"property_value_at_departure", "purchase_price", "acquisition_costs", "land_fmv_at_change_of_use",
                      "budget_low", "budget_high", "spent_to_date", "appraised_completed_value",
                      "expected_monthly_rent_per_unit", "hourly_rate"}
        date_keys = {"departure_date", "purchase_date", "change_of_use_date", "construction_start", "expected_completion", "spent_as_of"}
        decimal_keys = {"expected_operating_expense_ratio", "property_manager_fee_ratio", "permanent_mortgage_rate"}
        tuple_keys = {"gst_returns_outstanding", "gst_nil_returns_filed", "principal_residence_years", "options_under_consideration"}
        kwargs: dict[str, Any] = {}
        for k, v in raw.items():
            if k in ("complexes", "facilities", "phases"):
                continue
            if k not in cls.__dataclass_fields__:
                continue
            if k in money_keys:
                kwargs[k] = _m(v, cur) if k != "acquisition_costs" else (_m(v, cur) or Money.zero(cur))
            elif k in date_keys:
                kwargs[k] = _d(v)
            elif k in decimal_keys:
                if v not in (None, ""):
                    kwargs[k] = Decimal(str(v))
            elif k in tuple_keys:
                kwargs[k] = tuple(int(x) if k == "principal_residence_years" else str(x) for x in (v or ()))
            elif v is not None:
                kwargs[k] = v
        return cls(complexes=complexes, facilities=facilities, phases=phases, **kwargs)


def load_facts(path: Path) -> EngagementFacts:
    return EngagementFacts.from_dict(json.loads(Path(path).read_text()))


# The engagement letter's own figures with the identities replaced. Copy this
# to clients/<id>.engagement.json and put the real names in; that directory
# is never committed.
EXAMPLE_FACTS: dict[str, Any] = {
    "engagement_id": "ENG-FOURPLEX-EXAMPLE",
    "client_name": "Client A",
    "co_owner_name": "Co-owner B (spouse)",
    "property_label": "42 Example Crescent, Toronto",
    "province": "ON",
    "municipality": "Toronto",
    "client_citizenship": "unknown",
    "resident_country": "Italy",
    "departure_date": "2020-07-01",
    "last_t1_year": 2020,
    "departure_reported_on_t1": False,
    "later_t1_returns_filed": False,
    "residency_determination_done": False,
    "property_value_at_departure": "900000.00",
    "gst_registered": True,
    "gst_account_holder": "Client A (former sole proprietorship)",
    "gst_returns_outstanding": ["2021"],
    "gst_nil_returns_filed": ["2022", "2023", "2024", "2025"],
    "construction_itcs_claimed": False,
    "co_owner_registered": False,
    "purchase_date": "2018-06-01",
    "purchase_price": "690000.00",
    "acquisition_costs": "18000.00",
    "former_principal_residence": True,
    "principal_residence_years": [2018, 2019, 2020],
    "resident_in_acquisition_year": True,
    "building_demolished": True,
    "change_of_use_date": "2026-06-01",
    "land_fmv_at_change_of_use": "1150000.00",
    "owned_personally_jointly": True,
    "construction_start": "2026-06-01",
    "expected_completion": "2027-05-01",
    "budget_low": "1500000.00",
    "budget_high": "1600000.00",
    "budget_includes_hst": True,
    "spent_to_date": "250000.00",
    "spent_as_of": "2026-09-20",
    "appraised_completed_value": "2757000.00",
    "complexes": [
        {"name": "Fourplex", "units": 4, "kind": "multiple_unit", "detached": False, "expected_fmv": "2300000.00"},
        {"name": "Garden suite", "units": 1, "kind": "single_unit", "detached": True, "expected_fmv": "457000.00"},
    ],
    "facilities": [
        {"name": "Construction loan", "limit": "500000.00", "drawn": "250000.00", "annual_rate": "0.085"},
        {"name": "Construction facility (approved)", "limit": "1000000.00", "drawn": "0.00", "annual_rate": "0.085"},
    ],
    "contracts_directly_with_trades": True,
    "invoices_issued_to": "client personally",
    "expected_monthly_rent_per_unit": "2900.00",
    "expected_operating_expense_ratio": "0.35",
    "property_manager_fee_ratio": "0.05",
    "permanent_mortgage_rate": "0.05",
    "options_under_consideration": ["incorporation", "condominium conversion", "sale of units", "family transfer"],
    "hourly_rate": "200.00",
    "phases": [
        {"number": 1, "title": "Canadian Tax, Ownership + GST/HST Action Plan", "fee": "1500.00", "cadence": "fixed", "authorised": True,
         "deliverables": ["Residency and filing history review", "GST/HST account review and 2021 catch-up return",
                          "PBRH, NRRP and Ontario rebate assessment per complex", "Ownership comparison and written recommendation",
                          "Construction accounting structure, financing schedules, document workflow, GST/HST support schedule",
                          "Written action plan and implementation meeting"]},
        {"number": 2, "title": "Construction Accounting + Monthly Owner Finance Pack", "fee": "600.00", "cadence": "monthly", "authorised": False,
         "deliverables": ["Monthly project books and reconciliations", "Costs by category, contributions, draws, interest, GST/HST",
                          "GST/HST support file and routine compliance", "Monthly Owner Finance Pack", "Lender and property-manager coordination"]},
        {"number": 3, "title": "Completion, Self-Supply + Rental Rebate Filing", "fee": "2200.00", "cadence": "fixed", "authorised": False,
         "deliverables": ["Reconfirm qualifying facts and timing", "Appraisal and floor-area coordination",
                          "Self-supply and rebate calculations, federal and Ontario forms", "Routine CRA follow-up"]},
        {"number": 4, "title": "Ongoing Rental Accounting + Canadian Financial Management", "fee": "550.00", "cadence": "monthly", "authorised": False,
         "deliverables": ["Rental bookkeeping and reconciliations", "Income, expenses, financing, capex, property-manager activity",
                          "Property reporting and year-end schedules", "Non-resident rental administration and annual compliance package"]},
    ],
}
