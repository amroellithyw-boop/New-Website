"""Deterministic vendor knowledge, checked before any model is asked.

The legacy system carried a list of Canadian vendor patterns inside a prompt.
Here they are a table: each pattern names a category, a suggested account in
the reference chart, and the sales-tax treatment. Matching a bank description
against this table costs nothing and is right far more often than a model
guessing, and the model is only asked about what the table does not know.

The per-client cache remembers researched vendors and, more importantly, the
operator's corrections, which become precedents the risk router treats as
known patterns.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import TaxTreatment

__all__ = ["VendorHint", "KNOWN_VENDORS", "match_known_vendor", "VendorCache", "normalise_vendor"]


@dataclass(frozen=True)
class VendorHint:
    category: str
    account_number: str
    account_name: str
    tax_treatment: TaxTreatment
    personal_use_risk: bool = False
    note: str = ""
    source: str = "known_vendor_table"
    confidence: int = 90


def _h(cat, num, name, tax, risk=False, note="") -> VendorHint:
    return VendorHint(cat, num, name, tax, risk, note)


FUEL = _h("fuel", "5400", "Fuel", "taxable")
MEALS = _h("meals", "7300", "Meals and Entertainment", "meals_50", note="50% ITC only")
MATERIALS = _h("materials", "5100", "Materials and Supplies", "taxable")
UNIFORMS = _h("uniforms", "7700", "Uniforms and Workwear", "taxable")
OFFICE = _h("office_supplies", "6900", "Office Supplies", "taxable")
TELECOM = _h("telephone", "6600", "Telephone and Internet", "taxable")
UTILITIES = _h("utilities", "6500", "Utilities", "taxable")
SHIPPING = _h("shipping", "6910", "Shipping and Postage", "taxable")
INSURANCE = _h("insurance", "6300", "Insurance", "exempt", note="No ITC; insurance is exempt")
TAX_PAYMENT = _h("tax_payment", "2200", "GST/HST Payable", "no_tax_component", note="Remittance, not an expense")
WSIB = _h("wsib", "2300", "WSIB Payable", "no_tax_component")
PAYROLL_SVC = _h("payroll_service", "6920", "Payroll Processing", "taxable")
SOFTWARE = _h("software", "6910", "Software Subscriptions", "taxable")
MIXED = _h("mixed", "1999", "Ask My Accountant", "unknown", True, "Could be supplies, equipment or personal; needs receipt")
GROCERY = _h("groceries", "1999", "Ask My Accountant", "unknown", True, "Groceries are personal unless a clear business purpose exists")
AUTO_PARTS = _h("vehicle_maintenance", "6120", "Vehicle Repairs and Maintenance", "taxable")
VEHICLE_RENTAL = _h("vehicle_rental", "6130", "Vehicle Rental", "taxable")
BANK_FEES = _h("bank_charges", "7100", "Bank Charges", "exempt")
INTEREST = _h("interest", "7000", "Interest Expense", "exempt")
RENT = _h("rent", "6400", "Rent", "taxable")
EQUIPMENT_RENTAL = _h("equipment_rental", "5300", "Equipment Rental", "taxable")
HARDWARE = _h("materials", "5100", "Materials and Supplies", "taxable", True, "Canadian Tire and similar are mixed; tools may be capital")

KNOWN_VENDORS: tuple[tuple[str, VendorHint], ...] = (
    (r"petro[\s-]?can|esso|shell|sunoco|pioneer|irving|ultramar|costco gas|husky|chevron|mobil", FUEL),
    (r"tim horton|starbucks|mcdonald|subway|a&w|wendy|harvey|swiss chalet|pizza|restaurant|bistro|cafe|coffee", MEALS),
    (r"home depot|rona|lowe'?s|home hardware|kent building|beaver lumber|timber ?mart", MATERIALS),
    (r"cintas|aramark|g&k|unifirst", UNIFORMS),
    (r"uline|staples|grand ?& ?toy|bureau en gros", OFFICE),
    (r"rogers|bell canada|bell mobility|telus|shaw|cogeco|freedom mobile|koodo|videotron|fido", TELECOM),
    (r"hydro one|toronto hydro|enbridge|alectra|union gas|fortis|epcor|bc hydro", UTILITIES),
    (r"canada post|purolator|\bups\b|fedex|dhl", SHIPPING),
    (r"insurance|intact|aviva|wawanesa|bfl canada|bryson|lawrie|economical|co-operators|desjardins assur", INSURANCE),
    (r"canada revenue|\bcra\b|receiver general|revenu qu[ée]bec", TAX_PAYMENT),
    (r"\bwsib\b|workplace safety", WSIB),
    (r"\badp\b|ceridian|payworks|wagepoint|dayforce", PAYROLL_SVC),
    (r"quickbooks|intuit|\bqbo\b|xero|freshbooks|microsoft 365|google workspace|adobe|zoom|dropbox|jobber|housecall", SOFTWARE),
    (r"amazon|amzn", MIXED),
    (r"loblaw|metro\b|sobeys|food basics|no frills|freshco|walmart supercent|superstore|farm boy|longo", GROCERY),
    (r"costco(?! gas)", MIXED),
    (r"canadian tire|princess auto", HARDWARE),
    (r"napa|carquest|autoparts|auto parts|part ?source|kal tire|midas|jiffy lube|mr\.? lube", AUTO_PARTS),
    (r"enterprise rent|budget rent|hertz|discount car|avis", VEHICLE_RENTAL),
    (r"service charge|monthly fee|nsf|overdraft|wire fee|account fee|bank fee|\bfee\b", BANK_FEES),
    (r"interest charge|interest expense|\binterest\b", INTEREST),
    (r"united rentals|herc rentals|sunbelt|cooper equipment|battlefield", EQUIPMENT_RENTAL),
)


def normalise_vendor(text: str) -> str:
    """Strip the noise a bank puts around a merchant name."""
    cleaned = text.lower()
    cleaned = re.sub(r"\b(pos|purchase|point of sale|debit|visa|mastercard|interac|e-?transfer|payment to|payment)\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{2,}\b", " ", cleaned)  # store numbers, card fragments
    cleaned = re.sub(r"[^a-z&' ]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def match_known_vendor(description: str) -> VendorHint | None:
    """First matching pattern wins. Order in the table is therefore deliberate."""
    haystack = description.lower()
    for pattern, hint in KNOWN_VENDORS:
        if re.search(pattern, haystack):
            return hint
    return None


@dataclass
class VendorCache:
    """Per-client memory of vendor decisions.

    An operator correction overrides both the table and any research, and is
    what turns a novel pattern into a known one for the risk router.
    """

    client_id: str
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, client_id: str, directory: Path) -> VendorCache:
        path = Path(directory) / f"{client_id}.vendors.json"
        entries = json.loads(path.read_text()) if path.exists() else {}
        return cls(client_id=client_id, entries=entries, path=path)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True))

    @staticmethod
    def key_for(vendor: str) -> str:
        return normalise_vendor(vendor) or vendor.strip().lower()

    def get(self, vendor: str) -> VendorHint | None:
        entry = self.entries.get(self.key_for(vendor))
        if not entry:
            return None
        return VendorHint(
            category=entry["category"], account_number=entry["account_number"],
            account_name=entry["account_name"], tax_treatment=entry["tax_treatment"],
            personal_use_risk=bool(entry.get("personal_use_risk")), note=entry.get("note", ""),
            source=entry.get("source", "cache"), confidence=int(entry.get("confidence", 80)),
        )

    def remember(self, vendor: str, hint: VendorHint, *, decided_by: str = "model") -> None:
        self.entries[self.key_for(vendor)] = {
            "vendor": vendor, "category": hint.category, "account_number": hint.account_number,
            "account_name": hint.account_name, "tax_treatment": hint.tax_treatment,
            "personal_use_risk": hint.personal_use_risk, "note": hint.note,
            "source": hint.source, "confidence": hint.confidence, "decided_by": decided_by,
            "decided_at": datetime.now(UTC).isoformat(),
        }

    def correct(self, vendor: str, *, account_number: str, account_name: str,
                tax_treatment: TaxTreatment, category: str = "corrected", note: str = "") -> None:
        """An operator override. Highest authority, and a precedent from now on."""
        self.remember(
            vendor,
            VendorHint(category, account_number, account_name, tax_treatment, False, note,
                       source="operator", confidence=100),
            decided_by="operator",
        )

    def precedents(self) -> Iterable[str]:
        return (k for k, v in self.entries.items() if v.get("decided_by") == "operator")
