"""The Continuous Controller run: one command from ledger to reviewed findings.

This is the orchestration the blueprint calls the first wedge. It does, in order:

1. build the deterministic context and tie out the statements;
2. run the control catalogue;
3. score and route every finding;
4. drive the material ones through the review hierarchy;
5. return a result that can be rendered, audited or costed.

Step 1 is a hard gate. If the trial balance does not tie, the run stops and says
so rather than producing confident findings on top of numbers that do not add up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from .agents.gateway import ModelGateway
from .agents.roles import run_review_loop
from .canonical.enums import AutonomyLevel, RiskTier
from .canonical.models import Ledger
from .engine.reconcile import BankStatement
from .money import Money
from .rules import REGISTRY, RuleContext, RuleOutcome, run_rules
from .rules.base import Finding, RuleRegistry
from .workitems.risk import required_reviewers, score_finding
from .workitems.state import WorkItem

if False:  # typing only; avoids an import cycle at runtime
    from .clients.knowledge import ClientKnowledge
    from .clients.profile import ClientProfile

__all__ = ["DataGateFailure", "ControllerRun", "run_continuous_controller"]

DEFAULT_POLICY: dict[str, Any] = {
    "authorised_posters": {"sync", "payroll_sync", "close_process", "migration"},
    "sales_tax_rate": Decimal("0.13"),
    "depreciation_rate": Decimal("0.15"),
}


@dataclass(frozen=True)
class DataGateFailure:
    """Why the run refused to proceed past the data gate."""

    check: str
    detail: str
    amount: Money | None = None


@dataclass
class ControllerRun:
    """Everything one Continuous Controller pass produced."""

    entity_name: str
    period_start: date
    period_end: date
    currency: str
    ctx: RuleContext
    outcome: RuleOutcome
    work_items: list[WorkItem] = field(default_factory=list)
    gate_failures: tuple[DataGateFailure, ...] = ()
    gateway: ModelGateway | None = None
    reviewed: bool = False
    suppressed: list[Finding] = field(default_factory=list)

    @property
    def passed_data_gate(self) -> bool:
        return not self.gate_failures

    @property
    def findings(self) -> list[Finding]:
        return self.outcome.sorted_findings()

    def by_tier(self) -> dict[RiskTier, list[WorkItem]]:
        out: dict[RiskTier, list[WorkItem]] = {}
        for item in self.work_items:
            out.setdefault(item.risk.tier, []).append(item)
        return out

    @property
    def total_exposure(self) -> Money:
        return self.outcome.total_exposure(self.currency)

    @property
    def recoverable_cash(self) -> Money:
        """Exposure on findings where money can actually be recovered or collected.

        Kept separate from total exposure on purpose. Adding a covenant balance
        and a duplicate payment together produces a number that impresses and
        means nothing.
        """
        total = Money.zero(self.currency)
        for f in self.findings:
            if f.involves_disbursed_cash or f.category == "accounts receivable":
                total = total + abs(f.exposure)
        return total

    @property
    def items_needing_human(self) -> list[WorkItem]:
        return [i for i in self.work_items if i.plan.requires_human]

    @property
    def cost_micros(self) -> int:
        return sum(i.cost_micros for i in self.work_items)

    def summary(self) -> dict[str, Any]:
        sev: dict[str, int] = {}
        for f in self.findings:
            sev[f.severity.value] = sev.get(f.severity.value, 0) + 1
        tiers = {t.value: len(v) for t, v in sorted(self.by_tier().items(), key=lambda kv: kv[0].value)}
        return {
            "entity": self.entity_name,
            "period": f"{self.period_start.isoformat()}..{self.period_end.isoformat()}",
            "passed_data_gate": self.passed_data_gate,
            "gate_failures": [
                {"check": g.check, "detail": g.detail} for g in self.gate_failures
            ],
            "controls_run": self.outcome.rules_run,
            "controls_with_findings": self.outcome.rules_with_findings,
            "control_errors": self.outcome.errors,
            "findings": len(self.findings),
            "by_severity": sev,
            "by_risk_tier": tiers,
            "total_exposure": str(self.total_exposure.to_decimal()),
            "recoverable_cash": str(self.recoverable_cash.to_decimal()),
            "materiality": self.ctx.materiality.describe(),
            "industry": self.ctx.policy.get("industry_label"),
            "size_tier": self.ctx.policy.get("size_tier"),
            "reviewed": self.reviewed,
            "items_needing_human": len(self.items_needing_human),
            "suppressed_by_learning": len(self.suppressed),
            "cost_micros": self.cost_micros,
        }


def _check_data_gate(ctx: RuleContext) -> tuple[DataGateFailure, ...]:
    """The blueprint's data gate: do our numbers reproduce the source exactly?"""
    failures: list[DataGateFailure] = []
    if not ctx.tb.closing_imbalance.is_zero:
        failures.append(
            DataGateFailure(
                check="trial balance nets to zero",
                detail=(
                    "The sum of all closing balances is not zero, so the imported ledger "
                    "is incomplete or one-sided."
                ),
                amount=ctx.tb.closing_imbalance,
            )
        )
    if not ctx.tb.activity_imbalance.is_zero:
        failures.append(
            DataGateFailure(
                check="period debits equal credits",
                detail="Total debits do not equal total credits for the period.",
                amount=ctx.tb.activity_imbalance,
            )
        )
    if not ctx.bs.equation_difference.is_zero:
        failures.append(
            DataGateFailure(
                check="balance sheet equation",
                detail="Assets do not equal liabilities plus equity.",
                amount=ctx.bs.equation_difference,
            )
        )
    return tuple(failures)


def run_continuous_controller(
    ledger: Ledger,
    *,
    period_start: date,
    period_end: date,
    fiscal_year_start: date | None = None,
    statements: Sequence[BankStatement] = (),
    policy: dict[str, Any] | None = None,
    registry: RuleRegistry | None = None,
    gateway: ModelGateway | None = None,
    review: bool = False,
    review_min_tier: RiskTier = RiskTier.R3,
    review_limit: int = 10,
    autonomy: AutonomyLevel = AutonomyLevel.A0_OBSERVE,
    enforce_data_gate: bool = True,
    profile: ClientProfile | None = None,
    knowledge: ClientKnowledge | None = None,
    scope_to_profile: bool = False,
    learning: Any = None,
) -> ControllerRun:
    """Run a full Continuous Controller pass.

    ``review`` is off by default because a deterministic pass is useful on its
    own and costs nothing. When it is on, only items at or above
    ``review_min_tier`` go through the hierarchy, and no more than
    ``review_limit`` of them: spending model tokens on routine items is exactly
    the waste the constitution's token-control rule warns about.
    """
    # The profile is the single source of personalisation. When one is given it
    # supplies the policy, the fiscal year and, if asked, the control scope;
    # an explicit policy argument still wins for individual keys so an operator
    # can override one setting for one run without editing the profile.
    resolved_policy: dict[str, Any] = dict(DEFAULT_POLICY)
    if profile is not None:
        resolved_policy.update(profile.to_policy().as_rule_policy())
        if fiscal_year_start is None:
            fiscal_year_start = profile.fiscal_year_start(period_end)
        if knowledge is not None:
            resolved_policy["context_notes"] = "\n".join(
                [resolved_policy.get("context_notes", "")] + knowledge.context_lines()
            ).strip()
    if policy:
        resolved_policy.update(policy)
    fy_start = fiscal_year_start or date(period_end.year, 1, 1)
    ctx = RuleContext(
        ledger=ledger,
        period_start=period_start,
        period_end=period_end,
        fiscal_year_start=fy_start,
        statements=tuple(statements),
        policy=resolved_policy,
    ).prepare()

    gate_failures = _check_data_gate(ctx)
    only = None
    if scope_to_profile and profile is not None:
        only = profile.to_policy().controls_in_scope(registry or REGISTRY)
    outcome = run_rules(ctx, registry=registry or REGISTRY, only=only)

    # Learned suppressions: a pattern the operator dismissed three times on this
    # client stops being raised, unless it is critical. Recorded, never silent.
    suppressed_findings: list = []
    if learning is not None:
        from .learning import apply_learning

        kept, suppressed_findings = apply_learning(outcome.findings, learning)
        outcome.findings = kept

    run = ControllerRun(
        entity_name=ledger.entity.name,
        period_start=period_start,
        period_end=period_end,
        currency=ledger.currency,
        ctx=ctx,
        outcome=outcome,
        gate_failures=gate_failures,
        gateway=gateway,
        suppressed=suppressed_findings,
    )

    # Findings are still produced when the gate fails, because the integrity
    # controls are precisely what explains the failure. What does not happen is
    # spending model reasoning on top of numbers that do not add up.
    for index, finding in enumerate(run.findings):
        # A precedent the operator approved for this pattern lowers novelty in
        # the risk score. This is how the system gets cheaper on a client the
        # longer it works with them, without getting less careful on new things.
        known = bool(
            knowledge is not None
            and knowledge.is_known(
                f"rule:{finding.rule_id}",
                f"rule:{finding.rule_id}:{finding.finding_id.partition(':')[2]}",
            )
        ) or bool(learning is not None and learning.is_known(finding))
        score = score_finding(finding, ctx.materiality, known_pattern=known)
        plan = required_reviewers(finding, score)
        run.work_items.append(
            WorkItem(
                work_item_id=f"WI-{period_end.isoformat()}-{index + 1:04d}",
                tenant_id=ledger.tenant.tenant_id,
                entity_id=ledger.entity.entity_id,
                objective=finding.title,
                finding=finding,
                packet=finding.packet,
                risk=score,
                plan=plan,
                period_start=period_start,
                period_end=period_end,
                autonomy=autonomy,
            )
        )

    if review and gateway is not None:
        if enforce_data_gate and gate_failures:
            for item in run.work_items:
                item.record(
                    "orchestrator",
                    "review_skipped",
                    "the data gate failed; no model reasoning is applied until the "
                    "ledger ties out",
                )
        else:
            eligible = [
                i for i in run.work_items if i.risk.tier.level >= review_min_tier.level
            ]
            eligible.sort(key=lambda i: (-i.risk.tier.level, -abs(i.finding.exposure).minor_units))
            for item in eligible[:review_limit]:
                run_review_loop(item, gateway)
            run.reviewed = True

    return run
