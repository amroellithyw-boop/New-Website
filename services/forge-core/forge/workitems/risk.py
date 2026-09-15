"""Deterministic risk scoring and review routing.

Constitution rule #5: routine low-risk work must not consume CFO-level
reasoning. The router decides how far up the hierarchy an item travels, and it
does so with a *deterministic, inspectable* score rather than by asking a model
how risky something feels.

Two properties are deliberate:

* A reviewer may escalate a tier but never silently downgrade one. Downgrades
  require an explicit, recorded override.
* The score stores its reasons. "R3 because it moves cash, is above performance
  materiality and is irreversible" is reviewable; a bare 0.82 is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..canonical.enums import AgentRole, RiskTier
from ..rules.base import Finding
from ..rules.materiality import Materiality

__all__ = ["RiskFactor", "RiskScore", "score_finding", "required_reviewers", "ReviewPlan"]


@dataclass(frozen=True)
class RiskFactor:
    """One contribution to the risk score, with its reason recorded."""

    name: str
    weight: Decimal
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"factor": self.name, "weight": str(self.weight), "reason": self.reason}


@dataclass
class RiskScore:
    tier: RiskTier
    score: Decimal
    factors: tuple[RiskFactor, ...]
    floor_applied: RiskTier | None = None

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(f.reason for f in self.factors)

    def escalate_to(self, tier: RiskTier, reason: str) -> RiskScore:
        """Raise the tier. Lowering is not available on purpose."""
        if tier.level <= self.tier.level:
            return self
        return RiskScore(
            tier=tier,
            score=self.score,
            factors=self.factors
            + (RiskFactor(name="reviewer escalation", weight=Decimal(0), reason=reason),),
            floor_applied=self.floor_applied,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier.value,
            "score": str(self.score.quantize(Decimal("0.01"))),
            "factors": [f.to_dict() for f in self.factors],
            "floor_applied": self.floor_applied.value if self.floor_applied else None,
        }


# Weights are stated once, here, so that tuning the router is a reviewable diff
# rather than a change scattered across control code. The scale tops out near 10.
#
# The dominant question is NOT "how big is it" but "what does being wrong cost,
# and can it be undone". A large overdue invoice leads to a phone call and is
# fully reversible. A smaller unauthorised payment means money is already gone.
# Scoring on size alone sends the first to the CFO and both to the same queue,
# which is how a control system loses its reader.
WEIGHTS: dict[str, Decimal] = {
    "materiality": Decimal("2.5"),
    "severity": Decimal("2.0"),
    "disbursed_cash": Decimal("2.0"),
    "regulatory": Decimal("2.0"),
    "financial_statement": Decimal("1.0"),
    "evidence_gap": Decimal("1.5"),
    "low_confidence": Decimal("1.0"),
    "novelty": Decimal("0.5"),
}

REGULATORY_CATEGORIES = {"tax", "payroll"}
STATEMENT_CATEGORIES = {"integrity", "classification", "close", "fixed assets", "debt"}

TIER_CUTOFFS: tuple[tuple[Decimal, RiskTier], ...] = (
    (Decimal("7.0"), RiskTier.R4),
    (Decimal("5.0"), RiskTier.R3),
    (Decimal("3.0"), RiskTier.R2),
    (Decimal("1.0"), RiskTier.R1),
)

MATERIALITY_CAP = Decimal("5")


def score_finding(
    finding: Finding,
    materiality: Materiality,
    *,
    known_pattern: bool = False,
) -> RiskScore:
    """Score one finding and place it in a review tier.

    Every contribution records its reason. A tier with no stated reasons cannot
    be challenged by a reviewer, and an unchallengeable routing decision is just
    an opinion with a number attached.
    """
    factors: list[RiskFactor] = []
    total = Decimal(0)

    significance = materiality.significance(finding.exposure) or Decimal(0)
    if significance > 0:
        capped = min(significance, MATERIALITY_CAP)
        contribution = WEIGHTS["materiality"] * (capped / MATERIALITY_CAP)
        total += contribution
        factors.append(
            RiskFactor(
                "materiality",
                contribution,
                f"{abs(finding.exposure).format()} is {significance:.1f}x performance "
                f"materiality ({materiality.performance.format()})",
            )
        )

    sev_contribution = WEIGHTS["severity"] * Decimal(finding.severity.rank) / Decimal(4)
    if sev_contribution > 0:
        total += sev_contribution
        factors.append(
            RiskFactor(
                "severity", sev_contribution, f"control severity is {finding.severity.value}"
            )
        )

    if finding.involves_disbursed_cash:
        total += WEIGHTS["disbursed_cash"]
        factors.append(
            RiskFactor(
                "disbursed_cash",
                WEIGHTS["disbursed_cash"],
                "money has already left the business or a statutory deadline has passed, "
                "so remediation means recovery rather than a correction",
            )
        )

    if finding.category in REGULATORY_CATEGORIES:
        total += WEIGHTS["regulatory"]
        factors.append(
            RiskFactor(
                "regulatory",
                WEIGHTS["regulatory"],
                f"{finding.category} outcomes are filed with a tax authority and "
                "carry penalty exposure",
            )
        )

    if finding.category in STATEMENT_CATEGORIES:
        total += WEIGHTS["financial_statement"]
        factors.append(
            RiskFactor(
                "financial_statement",
                WEIGHTS["financial_statement"],
                "the issue changes reported financial position or result",
            )
        )

    if not finding.packet.is_sufficient:
        total += WEIGHTS["evidence_gap"]
        factors.append(
            RiskFactor(
                "evidence_gap",
                WEIGHTS["evidence_gap"],
                "the evidence packet lacks source references or calculations, so the "
                "conclusion cannot be reproduced",
            )
        )

    if finding.confidence < Decimal("0.7"):
        contribution = WEIGHTS["low_confidence"] * (Decimal(1) - finding.confidence)
        total += contribution
        factors.append(
            RiskFactor(
                "low_confidence",
                contribution,
                f"the control reports this as a signal at confidence {finding.confidence}, "
                "not a proven defect",
            )
        )

    if not known_pattern:
        total += WEIGHTS["novelty"]
        factors.append(
            RiskFactor(
                "novelty", WEIGHTS["novelty"], "no approved precedent exists for this pattern"
            )
        )

    tier = RiskTier.R0
    for cutoff, candidate in TIER_CUTOFFS:
        if total >= cutoff:
            tier = candidate
            break

    floor = finding.risk_tier_floor
    floor_applied = None
    if floor.level > tier.level:
        floor_applied = floor
        factors.append(
            RiskFactor(
                "control floor",
                Decimal(0),
                f"control {finding.rule_id} sets a minimum review tier of {floor.value}",
            )
        )
        tier = floor

    return RiskScore(tier=tier, score=total, factors=tuple(factors), floor_applied=floor_applied)


# Review chains per tier. Separation of duties is structural: the preparer never
# appears in its own review chain.
CHAINS: dict[RiskTier, tuple[AgentRole, ...]] = {
    RiskTier.R0: (),
    RiskTier.R1: (AgentRole.ACCOUNTING_MANAGER,),
    RiskTier.R2: (AgentRole.ACCOUNTING_MANAGER, AgentRole.CONTROLLER),
    RiskTier.R3: (
        AgentRole.ACCOUNTING_MANAGER,
        AgentRole.CONTROLLER,
        AgentRole.ADVERSARY,
    ),
    RiskTier.R4: (
        AgentRole.ACCOUNTING_MANAGER,
        AgentRole.CONTROLLER,
        AgentRole.ADVERSARY,
        AgentRole.POLICY_REVIEWER,
        AgentRole.VP_FINANCE,
        AgentRole.CFO,
        AgentRole.HUMAN,
    ),
}

# Which specialist prepares which category of work.
PREPARERS: dict[str, AgentRole] = {
    "integrity": AgentRole.CLOSE_ACCOUNTANT,
    "period control": AgentRole.CLOSE_ACCOUNTANT,
    "classification": AgentRole.BOOKKEEPER,
    "reconciliation": AgentRole.BOOKKEEPER,
    "accounts receivable": AgentRole.AR_SPECIALIST,
    "accounts payable": AgentRole.AP_SPECIALIST,
    "payroll": AgentRole.PAYROLL_SPECIALIST,
    "tax": AgentRole.TAX_SPECIALIST,
    "treasury": AgentRole.TREASURY_SPECIALIST,
    "debt": AgentRole.TREASURY_SPECIALIST,
    "fixed assets": AgentRole.CLOSE_ACCOUNTANT,
    "close": AgentRole.CLOSE_ACCOUNTANT,
    "variance": AgentRole.FPA_ANALYST,
    "planning": AgentRole.FPA_ANALYST,
    "risk": AgentRole.FPA_MANAGER,
}

# Specialists added to the chain for material work in their own domain.
SPECIALIST_FOR: dict[str, AgentRole] = {
    "tax": AgentRole.TAX_SPECIALIST,
    "payroll": AgentRole.PAYROLL_SPECIALIST,
    "treasury": AgentRole.TREASURY_SPECIALIST,
    "debt": AgentRole.TREASURY_SPECIALIST,
}


@dataclass(frozen=True)
class ReviewPlan:
    """Who prepares an item and who must review it before it can advance."""

    preparer: AgentRole
    reviewers: tuple[AgentRole, ...]
    tier: RiskTier
    requires_human: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier.value,
            "preparer": self.preparer.value,
            "reviewers": [r.value for r in self.reviewers],
            "requires_human_approval": self.requires_human,
        }


def required_reviewers(finding: Finding, score: RiskScore) -> ReviewPlan:
    """Build the review chain for a finding at its scored tier."""
    preparer = PREPARERS.get(finding.category, AgentRole.BOOKKEEPER)
    chain = list(CHAINS[score.tier])

    specialist = SPECIALIST_FOR.get(finding.category)
    if specialist and score.tier.level >= 3 and specialist not in chain:
        # Insert the domain specialist after the controller, before the adversary.
        idx = chain.index(AgentRole.CONTROLLER) + 1 if AgentRole.CONTROLLER in chain else len(chain)
        chain.insert(idx, specialist)

    # Separation of duties: the preparer cannot review its own work.
    chain = [r for r in chain if r is not preparer]

    return ReviewPlan(
        preparer=preparer,
        reviewers=tuple(chain),
        tier=score.tier,
        requires_human=AgentRole.HUMAN in chain,
    )
