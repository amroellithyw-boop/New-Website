"""The proposal: a diagnostic's findings turned into a reason to say yes.

The sales conversation leads with money found, not with software. The
proposal states what was found in the prospect's own books, what it is worth,
what the engagement costs, and the ratio between the two. All of it comes from
the review run; none of it is written by a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..clients.profile import ClientProfile
from ..firm import FirmConfig
from ..money import Money
from ..pipeline import ControllerRun
from .pricing import Engagement, recommend_engagement

__all__ = ["Proposal", "build_proposal", "render_proposal"]


@dataclass(frozen=True)
class Proposal:
    business_name: str
    prepared_on: date
    findings: int
    critical: int
    controls_run: int
    recoverable_cash: Money
    total_exposure: Money
    top_findings: tuple[str, ...]
    engagement: Engagement
    industry: str
    size_tier: str

    @property
    def first_year_return_multiple(self) -> str:
        r = self.recoverable_cash.ratio_to(self.engagement.annual_value)
        return f"{r:.1f}x" if r is not None else "n/a"

    def to_dict(self) -> dict:
        return {"business": self.business_name, "prepared_on": self.prepared_on.isoformat(), "findings": self.findings,
                "critical": self.critical, "recoverable_cash": str(self.recoverable_cash.to_decimal()),
                "total_exposure": str(self.total_exposure.to_decimal()), "top_findings": list(self.top_findings),
                "engagement": self.engagement.to_dict(), "return_multiple": self.first_year_return_multiple}


def build_proposal(profile: ClientProfile, run: ControllerRun, *, today: date | None = None, firm: FirmConfig | None = None) -> Proposal:
    findings = run.findings
    return Proposal(
        business_name=profile.business_name, prepared_on=today or date.today(), findings=len(findings), controls_run=run.outcome.rules_run,
        critical=sum(1 for f in findings if f.severity.value == "critical"),
        recoverable_cash=run.recoverable_cash, total_exposure=run.total_exposure,
        top_findings=tuple(f.title for f in findings[:5]), engagement=recommend_engagement(profile, firm),
        industry=run.ctx.policy.get("industry_label") or profile.industry_label, size_tier=profile.size_tier_label,
    )


def render_proposal(p: Proposal, *, firm: str, sender: str) -> str:
    e = p.engagement
    lines = [
        f"{p.business_name}: what we found, and what we propose",
        f"Prepared by {firm}, {p.prepared_on.strftime('%d %B %Y')}",
        "",
        f"We reviewed your books with {p.controls_run} controls. {p.findings} items came out, {p.critical} of them critical.",
        f"{p.recoverable_cash.format()} is money that has been overpaid, left unbilled, or is overdue from customers.",
        "",
        "The five that matter most:",
        *[f"  {i + 1}. {t}" for i, t in enumerate(p.top_findings)],
        "",
        f"What a {p.size_tier} {p.industry} business needs is a controller, not a bookkeeper. We propose the {e.tier_name} engagement:",
        *[f"  {label:<40s} {amt.format():>12s} / month" for label, amt in e.breakdown],
        f"  {'Monthly':<40s} {e.monthly_fee.format():>12s}",
    ]
    if e.cleanup_fee.minor_units:
        lines.append(f"  {'One-time cleanup':<40s} {e.cleanup_fee.format():>12s}")
    lines += [
        "",
        f"Against a first-year cost of {e.annual_value.format()}, the recoverable cash identified today is {p.recoverable_cash.format()}, "
        f"a {p.first_year_return_multiple} return before any of the ongoing benefit.",
        "",
        "Every number above traces to a source record in the attached review. Nothing here is estimated.",
        "",
        f"{sender}",
    ]
    return "\n".join(lines)
