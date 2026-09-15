"""Engagement pricing, from the profile, deterministically.

The legacy platform carried tiers and a fee field a person typed in. This
recommends the tier and the fee from the facts: revenue band, transaction
volume, services engaged, and cleanup backlog. Every number is a rule an
operator can read and change in one place. Fees are in the client's currency.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..clients.profile import SERVICES, ClientProfile
from ..firm import FirmConfig
from ..money import Money, msum

__all__ = ["Engagement", "recommend_engagement", "SERVICE_FEES", "diagnostic_fee"]

# Monthly base by revenue band (CAD).
BASE_BY_TIER: dict[str, str] = {"start-up": "350.00", "micro": "650.00", "small": "1250.00",
                                "mid-size": "2400.00", "upper mid-market": "4500.00", "unknown": "950.00"}
# Monthly add-on per service beyond bookkeeping.
SERVICE_FEES: dict[str, str] = {
    "hst": "150.00", "payroll": "45.00", "corporate_tax": "175.00", "management_rep": "300.00",
    "cfo_advisory": "900.00", "job_costing": "350.00", "accounts_rec": "200.00", "accounts_pay": "200.00",
    "wsib": "75.00", "year_end": "225.00", "cash_forecast": "250.00", "cleanup": "0.00",
}
PAYROLL_PER_EMPLOYEE = "18.00"
VOLUME_PER_100_TXNS = "120.00"
CLEANUP_PER_MONTH_BEHIND = "450.00"


@dataclass(frozen=True)
class Engagement:
    tier_name: str
    size_tier: str
    monthly_fee: Money
    cleanup_fee: Money
    diagnostic_fee: Money
    breakdown: tuple[tuple[str, Money], ...]
    annual_value: Money

    def to_dict(self) -> dict:
        return {"tier": self.tier_name, "size_tier": self.size_tier, "monthly_fee": str(self.monthly_fee.to_decimal()),
                "cleanup_fee": str(self.cleanup_fee.to_decimal()), "diagnostic_fee": str(self.diagnostic_fee.to_decimal()),
                "annual_value": str(self.annual_value.to_decimal()),
                "breakdown": [(k, str(v.to_decimal())) for k, v in self.breakdown]}


DIAGNOSTIC_BY_TIER: dict[str, str] = {"start-up": "950.00", "micro": "1500.00", "small": "2500.00",
                                      "mid-size": "4000.00", "upper mid-market": "6500.00"}


def diagnostic_fee(profile: ClientProfile, firm: FirmConfig | None = None) -> Money:
    table = {**DIAGNOSTIC_BY_TIER, **(firm.diagnostic_by_tier if firm else {})}
    return Money.from_decimal(table.get(profile.size_tier_label, "2000.00"), profile.currency)


def recommend_engagement(profile: ClientProfile, firm: FirmConfig | None = None) -> Engagement:
    """Price from the profile, with the firm's own table overriding the defaults."""
    cur = profile.currency
    tier = profile.size_tier_label
    base_by_tier = {**BASE_BY_TIER, **(firm.base_by_tier if firm else {})}
    service_fees = {**SERVICE_FEES, **(firm.service_fees if firm else {})}
    per_employee = (firm.payroll_per_employee if firm and firm.payroll_per_employee else PAYROLL_PER_EMPLOYEE)
    per_100 = (firm.volume_per_100_txns if firm and firm.volume_per_100_txns else VOLUME_PER_100_TXNS)
    per_month_behind = (firm.cleanup_per_month_behind if firm and firm.cleanup_per_month_behind else CLEANUP_PER_MONTH_BEHIND)
    parts: list[tuple[str, Money]] = [("Base, " + tier, Money.from_decimal(base_by_tier.get(tier, base_by_tier["unknown"]), cur))]
    for svc in profile.services:
        if svc in service_fees and svc != "bookkeeping":
            fee = Money.from_decimal(service_fees[svc], cur)
            if svc == "payroll":
                fee = fee + Money.from_decimal(per_employee, cur).scale(max(profile.headcount, 1))
            if fee.minor_units:
                parts.append((SERVICES[svc].label, fee))
    if profile.monthly_transactions_estimate and profile.monthly_transactions_estimate > 150:
        extra = (profile.monthly_transactions_estimate - 150 + 99) // 100
        parts.append((f"Volume, {extra * 100} transactions over 150", Money.from_decimal(per_100, cur).scale(extra)))
    monthly = msum((m for _, m in parts), cur)
    cleanup = Money.from_decimal(per_month_behind, cur).scale(profile.months_behind) if profile.months_behind else Money.zero(cur)
    name = "Starter" if monthly < Money.from_decimal("900.00", cur) else "Growth" if monthly < Money.from_decimal("2500.00", cur) else "Scale"
    return Engagement(name, tier, monthly, cleanup, diagnostic_fee(profile, firm), tuple(parts), monthly.scale(12) + cleanup)
