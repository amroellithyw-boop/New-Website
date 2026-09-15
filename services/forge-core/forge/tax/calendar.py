"""The compliance calendar, generated from the client's profile.

Every deadline a Canadian owner-managed business faces, derived from its fiscal
year, its filing frequencies and the services engaged. Dates follow the general
rules and are marked for confirmation where the authority assigns a frequency
individually. This is the list the operations queue works from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..clients.profile import ClientProfile
from ..money import Money

__all__ = ["Deadline", "tax_calendar", "hst_filing_frequency"]


@dataclass(frozen=True)
class Deadline:
    kind: str
    due_on: date
    description: str
    authority: str
    period_label: str = ""
    confirm: bool = False
    """True when the authority assigns the frequency and it must be checked on
    the client's account rather than inferred."""

    def days_until(self, today: date) -> int:
        return (self.due_on - today).days


def hst_filing_frequency(annual_revenue: Money | None) -> str:
    """Default assigned frequency by taxable supplies. The authority can differ."""
    if annual_revenue is None:
        return "quarterly"
    units = annual_revenue.minor_units
    if units > 600_000_000:
        return "monthly"
    if units > 150_000_000:
        return "quarterly"
    return "annual"


def _month_end(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def _add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return _month_end(y, m) if d.day >= 28 else date(y, m, min(d.day, 28))


def tax_calendar(profile: ClientProfile, *, year: int, today: date | None = None) -> list[Deadline]:
    out: list[Deadline] = []
    fye = date(year, profile.fiscal_year_end_month, profile.fiscal_year_end_day)
    services = set(profile.services)

    # Sales tax.
    if profile.hst_registered and ("hst" in services or "bookkeeping" in services):
        freq = hst_filing_frequency(profile.annual_revenue_estimate)
        label = profile.sales_tax.label
        if freq == "monthly":
            for m in range(1, 13):
                pe = _month_end(year, m)
                out.append(Deadline("sales_tax_return", _add_months(pe, 1), f"{label} return and payment for {pe.strftime('%B %Y')}", "CRA", pe.strftime("%Y-%m"), confirm=True))
        elif freq == "quarterly":
            for q in range(4):
                pe = _month_end(year, 3 * (q + 1))
                out.append(Deadline("sales_tax_return", _add_months(pe, 1), f"{label} return and payment for Q{q + 1} {year}", "CRA", f"{year}-Q{q + 1}", confirm=True))
        else:
            out.append(Deadline("sales_tax_return", _add_months(fye, 3), f"Annual {label} return and payment for the year ended {fye.isoformat()}", "CRA", str(year), confirm=True))

    # Payroll.
    if "payroll" in services or profile.headcount > 0:
        for m in range(1, 13):
            pe = _month_end(year, m)
            out.append(Deadline("payroll_remittance", date(year + (m == 12), m % 12 + 1, 15), f"Source deductions for {pe.strftime('%B %Y')}", "CRA", pe.strftime("%Y-%m"), confirm=True))
        out.append(Deadline("t4_filing", date(year + 1, 2, 28), f"T4 and T4 Summary for {year}", "CRA", str(year)))
        out.append(Deadline("roe", fye, "Records of employment issued within five days of any interruption of earnings", "Service Canada", str(year)))
    if "wsib" in services or profile.wsib_number:
        for q in range(4):
            pe = _month_end(year, 3 * (q + 1))
            out.append(Deadline("wsib_premium", _add_months(pe, 1), f"WSIB premium report and payment for Q{q + 1} {year}", "WSIB", f"{year}-Q{q + 1}", confirm=True))
        out.append(Deadline("wsib_reconciliation", date(year + 1, 3, 31), f"WSIB annual reconciliation for {year}", "WSIB", str(year)))

    # Subcontractor reporting.
    if profile.files_t5018:
        out.append(Deadline("t5018", _add_months(fye, 6), f"T5018 contract payment slips for the year ended {fye.isoformat()}", "CRA", str(year)))
    elif profile.uses_subcontractors:
        out.append(Deadline("t4a", date(year + 1, 2, 28), f"T4A slips for unincorporated contractors paid $500 or more in {year}", "CRA", str(year)))

    # Corporate income tax.
    if profile.legal_structure.upper() in ("CCPC", "CORPORATION", "CORP", "INC"):
        out.append(Deadline("t2_balance_due", _add_months(fye, 3), f"Corporate tax balance due for the year ended {fye.isoformat()}", "CRA", str(year)))
        out.append(Deadline("t2_filing", _add_months(fye, 6), f"T2 corporate return for the year ended {fye.isoformat()}", "CRA", str(year)))
        for q in range(4):
            out.append(Deadline("corporate_instalment", _add_months(date(year, 1, 1) - timedelta(days=1), 3 * (q + 1)), f"Corporate tax instalment Q{q + 1} (only if prior-year tax exceeded the threshold)", "CRA", f"{year}-Q{q + 1}", confirm=True))
    out.sort(key=lambda d: d.due_on)
    if today:
        out = [d for d in out if d.due_on >= today - timedelta(days=60)]
    return out
