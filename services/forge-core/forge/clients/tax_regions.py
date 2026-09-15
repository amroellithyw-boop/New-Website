"""Canadian sales tax by province, and the corporate rates the CFO layer needs.

Carried over from the legacy platform's tax table and rebuilt as data with
Decimal rates. Verified April 2026 per the legacy note; treat as data that is
checked annually, not as truth.

The point of holding this as a table rather than a constant is that the rules
engine reads the client's province from their profile and the right rate flows
into every tax control automatically. A Nova Scotia client is not measured
against Ontario's 13%.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["SalesTaxRegime", "SALES_TAX", "sales_tax_for", "CorporateRates", "ONTARIO_CCPC"]


@dataclass(frozen=True)
class SalesTaxRegime:
    province: str
    name: str
    label: str
    rate: Decimal
    federal: Decimal
    provincial: Decimal

    @property
    def is_harmonised(self) -> bool:
        return self.label == "HST"

    @property
    def percent_label(self) -> str:
        return f"{self.rate * 100:.3f}".rstrip("0").rstrip(".") + "%"


def _r(p: str, name: str, label: str, rate: str, fed: str, prov: str) -> SalesTaxRegime:
    return SalesTaxRegime(p, name, label, Decimal(rate), Decimal(fed), Decimal(prov))


SALES_TAX: dict[str, SalesTaxRegime] = {
    "ON": _r("ON", "Ontario", "HST", "0.13", "0.05", "0.08"),
    "BC": _r("BC", "British Columbia", "GST+PST", "0.12", "0.05", "0.07"),
    "AB": _r("AB", "Alberta", "GST", "0.05", "0.05", "0"),
    "QC": _r("QC", "Quebec", "GST+QST", "0.14975", "0.05", "0.09975"),
    "NS": _r("NS", "Nova Scotia", "HST", "0.15", "0.05", "0.10"),
    "NB": _r("NB", "New Brunswick", "HST", "0.15", "0.05", "0.10"),
    "NL": _r("NL", "Newfoundland and Labrador", "HST", "0.15", "0.05", "0.10"),
    "PE": _r("PE", "Prince Edward Island", "HST", "0.15", "0.05", "0.10"),
    "MB": _r("MB", "Manitoba", "GST+PST", "0.12", "0.05", "0.07"),
    "SK": _r("SK", "Saskatchewan", "GST+PST", "0.11", "0.05", "0.06"),
    "YT": _r("YT", "Yukon", "GST", "0.05", "0.05", "0"),
    "NT": _r("NT", "Northwest Territories", "GST", "0.05", "0.05", "0"),
    "NU": _r("NU", "Nunavut", "GST", "0.05", "0.05", "0"),
}


def sales_tax_for(province: str | None) -> SalesTaxRegime:
    """Look up a province, defaulting to Ontario, the practice's home market."""
    return SALES_TAX.get((province or "ON").upper(), SALES_TAX["ON"])


@dataclass(frozen=True)
class CorporateRates:
    """Small-business and general corporate rates for a CCPC."""

    federal_small_business: Decimal
    provincial_small_business: Decimal
    federal_general: Decimal
    provincial_general: Decimal
    small_business_limit: Decimal

    @property
    def combined_small_business(self) -> Decimal:
        return self.federal_small_business + self.provincial_small_business

    @property
    def combined_general(self) -> Decimal:
        return self.federal_general + self.provincial_general


ONTARIO_CCPC = CorporateRates(
    federal_small_business=Decimal("0.09"),
    provincial_small_business=Decimal("0.032"),
    federal_general=Decimal("0.15"),
    provincial_general=Decimal("0.115"),
    small_business_limit=Decimal("500000"),
)
