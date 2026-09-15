"""The control framework.

A control is a *versioned object*, not a function buried in a report. Each one
declares what it checks, what evidence it needs, how severe a breach is, and how
to remediate it. That metadata is what lets ForgeBench measure a control, lets
the risk router decide how much review a finding deserves, and lets a client be
told precisely why something was raised.

Controls are pure: they read a :class:`RuleContext` and return findings. They do
not mutate the ledger, call a model, or perform I/O.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..canonical.enums import RiskTier, Severity
from ..canonical.models import Ledger, Period
from ..engine.aging import AgingReport, build_aging
from ..engine.reconcile import BankStatement, ReconciliationResult, reconcile
from ..engine.statements import (
    BalanceSheet,
    IncomeStatement,
    build_balance_sheet,
    build_income_statement,
)
from ..engine.trial_balance import TrialBalance, build_trial_balance
from ..evidence.packet import EvidencePacket, PacketBuilder
from ..money import Money
from .materiality import Materiality, compute_materiality

__all__ = [
    "RuleContext",
    "Finding",
    "Rule",
    "RuleRegistry",
    "REGISTRY",
    "register",
    "RuleOutcome",
    "run_rules",
]


@dataclass
class RuleContext:
    """Everything the control library needs, computed once per run.

    Building the trial balances, statements, agings and reconciliations a single
    time and sharing them is the difference between a control suite that runs in
    milliseconds and one that re-reads the ledger fifty times.
    """

    ledger: Ledger
    period_start: date
    period_end: date
    fiscal_year_start: date
    statements: tuple[BankStatement, ...] = ()
    today: date | None = None
    policy: dict[str, Any] = field(default_factory=dict)

    # populated by prepare()
    tb: TrialBalance = field(init=False)
    tb_prior: TrialBalance = field(init=False)
    tb_ytd: TrialBalance = field(init=False)
    tb_ttm: TrialBalance = field(init=False)
    pl: IncomeStatement = field(init=False)
    pl_prior: IncomeStatement = field(init=False)
    pl_ytd: IncomeStatement = field(init=False)
    pl_ttm: IncomeStatement = field(init=False)
    bs: BalanceSheet = field(init=False)
    ar: AgingReport = field(init=False)
    ap: AgingReport = field(init=False)
    materiality: Materiality = field(init=False)
    reconciliations: dict[str, ReconciliationResult] = field(init=False)

    def prepare(self) -> RuleContext:
        self.ledger.build_indexes()
        self.tb = build_trial_balance(self.ledger, self.period_start, self.period_end)
        prior_end = self.period_start - timedelta(days=1)
        span = (self.period_end - self.period_start).days
        self.tb_prior = build_trial_balance(
            self.ledger, prior_end - timedelta(days=span), prior_end
        )
        self.tb_ytd = build_trial_balance(self.ledger, self.fiscal_year_start, self.period_end)
        self.tb_ttm = build_trial_balance(
            self.ledger, self.period_end - timedelta(days=364), self.period_end
        )
        self.pl = build_income_statement(self.tb)
        self.pl_prior = build_income_statement(self.tb_prior)
        self.pl_ytd = build_income_statement(self.tb_ytd)
        self.pl_ttm = build_income_statement(self.tb_ttm)
        self.bs = build_balance_sheet(
            self.ledger, self.period_end, fiscal_year_start=self.fiscal_year_start
        )
        self.ar = build_aging(self.ledger, self.period_end, "receivable")
        self.ap = build_aging(self.ledger, self.period_end, "payable")
        # Materiality is anchored to trailing twelve months, not year to date. A
        # seasonal contractor closing January would otherwise be measured against
        # one month of snow revenue and every rounding difference would look material.
        self.materiality = compute_materiality(
            revenue=self.pl_ttm.revenue.total,
            total_assets=self.bs.total_assets,
            net_income=self.pl_ttm.net_income,
            currency=self.ledger.currency,
        )
        self.reconciliations = {}
        for st in self.statements:
            if st.end == self.period_end or (
                self.period_start <= st.end <= self.period_end
            ):
                self.reconciliations[st.bank_account_id] = reconcile(self.ledger, st)
        if self.today is None:
            self.today = self.period_end
        return self

    # ---- convenience ---------------------------------------------------

    @property
    def currency(self) -> str:
        return self.ledger.currency

    @property
    def entity_id(self) -> str:
        return self.ledger.entity.entity_id

    def zero(self) -> Money:
        return Money.zero(self.currency)

    def period_label(self) -> str:
        return f"{self.period_start.isoformat()} to {self.period_end.isoformat()}"

    def closed_periods(self) -> list[Period]:
        return [p for p in self.ledger.calendar.periods if p.is_closed]

    def is_in_closed_period(self, d: date) -> bool:
        p = self.ledger.calendar.period_for(d)
        return bool(p and p.is_closed)

    def drivers(
        self, account_id: str, *, start: date | None = None, end: date | None = None, limit: int = 8
    ) -> list:
        """The largest postings to an account in a window, biggest first.

        Analytical controls need these. "Repairs rose 2,945%" is a number; "repairs
        rose because of this one 58,000 invoice from Cedarline Hardware" is a
        finding someone can act on, and only the second one satisfies the
        evidence rule.
        """
        lo = start or self.period_start
        hi = end or self.period_end
        rows = [
            (txn, line)
            for (txn, line) in self.ledger.postings(account_id)
            if lo <= txn.txn_date <= hi
        ]
        rows.sort(key=lambda tl: -abs(tl[1].amount).minor_units)
        return rows[:limit]

    def packet(self, packet_id: str, objective: str) -> PacketBuilder:
        return PacketBuilder(
            packet_id=packet_id,
            entity_id=self.entity_id,
            objective=objective,
            period_start=self.period_start,
            period_end=self.period_end,
            currency=self.currency,
        )


@dataclass
class Finding:
    """One control breach, with everything needed to act on it."""

    finding_id: str
    rule_id: str
    rule_version: str
    title: str
    narrative: str
    severity: Severity
    category: str
    exposure: Money
    """Money at risk or misstated. Drives triage and the risk score."""
    packet: EvidencePacket
    remediation: str
    risk_tier_floor: RiskTier = RiskTier.R1
    confidence: Decimal = Decimal("1.0")
    """Deterministic controls are certain that the *condition* holds. Confidence
    below 1.0 means the condition is a heuristic signal, not a proven defect."""
    tags: tuple[str, ...] = ()
    involves_disbursed_cash: bool = False
    detected_at: date | None = None
    seeded_error_id: str | None = None
    """Set only by ForgeBench when matching a finding to a planted error."""

    @property
    def is_material(self) -> bool:
        return self.severity.rank >= Severity.HIGH.rank

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "title": self.title,
            "narrative": self.narrative,
            "severity": self.severity.value,
            "category": self.category,
            "exposure": str(self.exposure.to_decimal()),
            "currency": self.exposure.currency,
            "remediation": self.remediation,
            "risk_tier_floor": self.risk_tier_floor.value,
            "confidence": str(self.confidence),
            "tags": list(self.tags),
            "evidence": self.packet.to_dict(),
        }


class Rule(ABC):
    """Base class for every control.

    Subclasses set the metadata as class attributes and implement
    :meth:`evaluate`. Keeping metadata declarative means the rule catalogue can
    be rendered, tested and reported on without executing anything.
    """

    rule_id: str = "FOS-R000"
    version: str = "1"
    title: str = ""
    purpose: str = ""
    category: str = "general"
    severity: Severity = Severity.MEDIUM
    risk_tier_floor: RiskTier = RiskTier.R1
    remediation: str = ""
    evidence_required: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    involves_disbursed_cash: bool = False
    """True when the defect means money has already left the business or a
    statutory obligation is already overdue.

    This is the difference between a finding that costs an email and one that
    costs a recovery action, and it is the single biggest input to how far up the
    review hierarchy an item travels. Declaring it on the control keeps the risk
    router from having to guess from a category name."""

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        """Yield a finding for each breach. Yield nothing when the control passes."""

    # ---- helpers for subclasses ----------------------------------------

    def finding(
        self,
        ctx: RuleContext,
        *,
        suffix: str,
        title: str,
        narrative: str,
        exposure: Money,
        packet: EvidencePacket,
        severity: Severity | None = None,
        remediation: str | None = None,
        confidence: Decimal = Decimal("1.0"),
        tags: Sequence[str] = (),
        risk_tier_floor: RiskTier | None = None,
    ) -> Finding:
        return Finding(
            finding_id=f"{self.rule_id}:{suffix}",
            rule_id=self.rule_id,
            rule_version=self.version,
            title=title,
            narrative=narrative,
            severity=severity or self.severity,
            category=self.category,
            exposure=exposure,
            packet=packet,
            remediation=remediation or self.remediation,
            risk_tier_floor=risk_tier_floor or self.risk_tier_floor,
            confidence=confidence,
            tags=tuple(tags),
            involves_disbursed_cash=self.involves_disbursed_cash,
            detected_at=ctx.period_end,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "title": self.title,
            "purpose": self.purpose,
            "category": self.category,
            "severity": self.severity.value,
            "risk_tier_floor": self.risk_tier_floor.value,
            "evidence_required": list(self.evidence_required),
            "references": list(self.references),
            "involves_disbursed_cash": self.involves_disbursed_cash,
        }


class RuleRegistry:
    """Ordered catalogue of controls, keyed by rule id."""

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    def add(self, rule: Rule) -> Rule:
        if rule.rule_id in self._rules:
            raise ValueError(f"Duplicate rule id {rule.rule_id}")
        self._rules[rule.rule_id] = rule
        return rule

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self) -> Iterator[Rule]:
        return iter(sorted(self._rules.values(), key=lambda r: r.rule_id))

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    def by_category(self) -> dict[str, list[Rule]]:
        out: dict[str, list[Rule]] = {}
        for rule in self:
            out.setdefault(rule.category, []).append(rule)
        return out

    def catalogue(self) -> list[dict[str, Any]]:
        return [r.describe() for r in self]


REGISTRY = RuleRegistry()


def register(cls: type[Rule]) -> type[Rule]:
    """Class decorator that instantiates and registers a control."""
    REGISTRY.add(cls())
    return cls


@dataclass
class RuleOutcome:
    """Result of running the catalogue: findings plus per-rule execution facts."""

    findings: list[Finding] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    rules_run: int = 0
    rules_with_findings: int = 0
    suppressed_trivial: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_severity(self) -> dict[Severity, list[Finding]]:
        out: dict[Severity, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.severity, []).append(f)
        return out

    def total_exposure(self, currency: str = "CAD") -> Money:
        total = Money.zero(currency)
        for f in self.findings:
            total = total + abs(f.exposure)
        return total

    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.CRITICAL]

    def sorted_findings(self) -> list[Finding]:
        return sorted(
            self.findings,
            key=lambda f: (-f.severity.rank, -abs(f.exposure).minor_units, f.finding_id),
        )


def run_rules(
    ctx: RuleContext,
    *,
    registry: RuleRegistry | None = None,
    only: Sequence[str] | None = None,
    suppress_trivial: bool = True,
) -> RuleOutcome:
    """Run the control catalogue over a prepared context.

    A control that raises is recorded as an error and never silently skipped: a
    broken control is itself a finding about the system, and hiding it would let
    coverage quietly rot.
    """
    reg = registry or REGISTRY
    wanted = set(only) if only else None
    outcome = RuleOutcome()
    for rule in reg:
        if wanted is not None and rule.rule_id not in wanted:
            continue
        outcome.rules_run += 1
        try:
            produced = list(rule.evaluate(ctx))
        except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
            outcome.errors[rule.rule_id] = f"{type(exc).__name__}: {exc}"
            continue
        kept = []
        for f in produced:
            if (
                suppress_trivial
                and f.severity.rank < Severity.HIGH.rank
                and ctx.materiality.is_trivial(f.exposure)
            ):
                outcome.suppressed_trivial += 1
                continue
            kept.append(f)
        if kept:
            outcome.rules_with_findings += 1
            outcome.findings.extend(kept)
    return outcome
