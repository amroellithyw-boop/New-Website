"""ForgeBench: the quality system.

Nothing ships because it looked good. A change to a control, a threshold, a
prompt or a model must be measured against cases with known expected outcomes,
and a regression must block the deploy.

The runner matches findings to planted errors by *evidence*, not by text. A
finding counts as detecting a planted error when it comes from a rule the error
expects and its evidence packet references one of the transactions or accounts
the error touched. That rules out a control accidentally scoring because it
happened to mention the right number.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..money import Money
from ..rules import REGISTRY, RuleContext, RuleOutcome, run_rules
from ..rules.base import Finding, RuleRegistry
from .seeder import SeededCase, SeededError

__all__ = ["Detection", "BenchResult", "BenchThresholds", "run_bench", "DEFAULT_POLICY"]


DEFAULT_POLICY: dict[str, Any] = {
    "authorised_posters": {"sync", "payroll_sync", "close_process", "migration"},
    "sales_tax_rate": Decimal("0.13"),
    "depreciation_rate": Decimal("0.15"),
}


@dataclass(frozen=True)
class Detection:
    """Whether one planted error was found, and by what."""

    error: SeededError
    findings: tuple[Finding, ...]

    @property
    def detected(self) -> bool:
        return bool(self.findings)

    @property
    def detected_by(self) -> tuple[str, ...]:
        return tuple(sorted({f.rule_id for f in self.findings}))


@dataclass
class BenchResult:
    """Everything a release gate needs to decide."""

    case_name: str
    planted: tuple[SeededError, ...]
    decoys: tuple[SeededError, ...]
    detections: tuple[Detection, ...]
    decoy_hits: tuple[Detection, ...]
    outcome: RuleOutcome
    unexplained_findings: tuple[Finding, ...]
    duration_ms: float
    currency: str = "CAD"

    # ---- headline metrics ---------------------------------------------

    @property
    def total_planted(self) -> int:
        return len(self.planted)

    @property
    def detected_count(self) -> int:
        return sum(1 for d in self.detections if d.detected)

    @property
    def recall(self) -> Decimal:
        if not self.planted:
            return Decimal(1)
        return Decimal(self.detected_count) / Decimal(len(self.planted))

    @property
    def critical_planted(self) -> tuple[SeededError, ...]:
        return tuple(e for e in self.planted if e.is_critical)

    @property
    def critical_recall(self) -> Decimal:
        criticals = self.critical_planted
        if not criticals:
            return Decimal(1)
        ids = {e.error_id for e in criticals}
        found = sum(1 for d in self.detections if d.detected and d.error.error_id in ids)
        return Decimal(found) / Decimal(len(criticals))

    @property
    def missed(self) -> tuple[SeededError, ...]:
        return tuple(d.error for d in self.detections if not d.detected)

    @property
    def decoy_false_positives(self) -> int:
        return sum(1 for d in self.decoy_hits if d.detected)

    @property
    def noise_count(self) -> int:
        """Findings that match neither a planted error nor a decoy.

        These are not automatically wrong: the generated book contains genuine
        overdue invoices. They are what a human would have to triage, so the gate
        caps them rather than demanding zero.
        """
        return len(self.unexplained_findings)

    @property
    def precision(self) -> Decimal:
        total = len(self.outcome.findings)
        if total == 0:
            return Decimal(1)
        explained = total - self.noise_count - self.decoy_false_positives
        return Decimal(max(explained, 0)) / Decimal(total)

    @property
    def value_found(self) -> Money:
        total = Money.zero(self.currency)
        for d in self.detections:
            if d.detected:
                total = total + abs(d.error.amount)
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case_name,
            "planted": self.total_planted,
            "detected": self.detected_count,
            "recall": f"{self.recall:.3f}",
            "critical_planted": len(self.critical_planted),
            "critical_recall": f"{self.critical_recall:.3f}",
            "missed": [
                {"id": e.error_id, "kind": e.kind, "expected_rules": list(e.expected_rules)}
                for e in self.missed
            ],
            "decoy_false_positives": self.decoy_false_positives,
            "unexplained_findings": self.noise_count,
            "precision": f"{self.precision:.3f}",
            "total_findings": len(self.outcome.findings),
            "rule_errors": self.outcome.errors,
            "value_found": str(self.value_found.to_decimal()),
            "duration_ms": round(self.duration_ms, 1),
            "detections": [
                {
                    "id": d.error.error_id,
                    "kind": d.error.kind,
                    "detected": d.detected,
                    "by": list(d.detected_by),
                }
                for d in self.detections
            ],
        }

    def report(self) -> str:
        lines = [
            f"ForgeBench: {self.case_name}",
            f"  planted errors        {self.detected_count}/{self.total_planted} detected"
            f"  (recall {self.recall:.1%})",
            f"  critical errors       "
            f"{sum(1 for d in self.detections if d.detected and d.error.is_critical)}"
            f"/{len(self.critical_planted)} detected  (recall {self.critical_recall:.1%})",
            f"  decoys reported       {self.decoy_false_positives}/{len(self.decoys)}",
            f"  unexplained findings  {self.noise_count}",
            f"  total findings        {len(self.outcome.findings)}",
            f"  value identified      {self.value_found.format()}",
            f"  run time              {self.duration_ms:.0f} ms",
        ]
        if self.missed:
            lines.append("  MISSED:")
            for e in self.missed:
                lines.append(
                    f"    {e.error_id} {e.kind} ({e.amount.format()}) "
                    f"expected {', '.join(e.expected_rules) or 'any'}"
                )
        if self.outcome.errors:
            lines.append("  RULE ERRORS:")
            for rid, msg in self.outcome.errors.items():
                lines.append(f"    {rid}: {msg}")
        return "\n".join(lines)


@dataclass(frozen=True)
class BenchThresholds:
    """The release gate. A run below any of these must not ship."""

    min_critical_recall: Decimal = Decimal("1.0")
    min_recall: Decimal = Decimal("0.85")
    max_decoy_false_positives: int = 0
    max_unexplained_findings: int = 12
    allow_rule_errors: bool = False

    def evaluate(self, result: BenchResult) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if result.critical_recall < self.min_critical_recall:
            failures.append(
                f"critical recall {result.critical_recall:.1%} below "
                f"{self.min_critical_recall:.1%}"
            )
        if result.recall < self.min_recall:
            failures.append(f"recall {result.recall:.1%} below {self.min_recall:.1%}")
        if result.decoy_false_positives > self.max_decoy_false_positives:
            failures.append(
                f"{result.decoy_false_positives} decoy(s) reported, limit "
                f"{self.max_decoy_false_positives}"
            )
        if result.noise_count > self.max_unexplained_findings:
            failures.append(
                f"{result.noise_count} unexplained findings, limit "
                f"{self.max_unexplained_findings}"
            )
        if result.outcome.errors and not self.allow_rule_errors:
            failures.append(f"{len(result.outcome.errors)} control(s) raised an exception")
        return (not failures), failures


def _suffix(finding: Finding) -> str:
    _, _, suffix = finding.finding_id.partition(":")
    return suffix


def _identifies(finding: Finding, identifier: str) -> bool:
    """Whether a finding names one specific record or account.

    The suffix is compared as a whole token rather than by substring. A
    substring test would let account "1000" match a finding about invoice
    "INV-41000", which would inflate every detection score in the suite.
    """
    if identifier in {r.ref_id for r in finding.packet.refs}:
        return True
    suffix = _suffix(finding)
    if suffix == identifier:
        return True
    return identifier in suffix.split("-") or suffix.startswith(f"{identifier}-") or suffix.endswith(
        f"-{identifier}"
    )


def _finding_touches(finding: Finding, error: SeededError) -> bool:
    """True when the finding's evidence actually points at the planted error.

    Matching on evidence rather than on rule id alone is what stops a control
    scoring a detection because it fired for an unrelated reason in the same run.
    """
    identifiers = tuple(error.txn_ids) + tuple(error.account_ids)
    if not identifiers:
        return True
    return any(_identifies(finding, i) for i in identifiers)


def _match(findings: Sequence[Finding], error: SeededError) -> tuple[Finding, ...]:
    """Findings that constitute *detection* of this error.

    A control in the error's expected set counts only when its evidence points at
    the planted records, unless the error is one of the absence-defined cases
    that declares rule-only matching.
    """
    expected = set(error.expected_rules)
    matched = []
    for f in findings:
        if expected and f.rule_id not in expected:
            continue
        if not (error.match_by_rule_only or _finding_touches(f, error)):
            continue
        matched.append(f)
    return tuple(matched)


def _explains(findings: Sequence[Finding], error: SeededError) -> tuple[Finding, ...]:
    """Findings *attributable* to this error, including corroborating controls.

    A single defect legitimately trips several controls. Counting the secondary
    ones as noise would push the design toward fewer controls, which is the
    opposite of what a control system should optimise for.
    """
    rules = set(error.all_rules)
    out = []
    for f in findings:
        if f.rule_id not in rules:
            continue
        if error.match_by_rule_only or _finding_touches(f, error):
            out.append(f)
    return tuple(out)


DECOY_DOMINANCE = 0.5


def _match_decoy(findings: Sequence[Finding], decoy: SeededError) -> tuple[Finding, ...]:
    """A decoy is 'hit' when a finding is ABOUT it, not merely when it cites it.

    An analytical control legitimately names a large invoice as one of several
    drivers of a revenue movement. That is the control doing its job, and scoring
    it as a false positive would push the design toward findings that cite no
    evidence at all - the exact opposite of the evidence rule.

    So a decoy counts as reported only when the finding identifies it directly,
    or when the decoy's records make up the majority of what the finding points at.
    """
    identifiers = set(decoy.txn_ids) | set(decoy.account_ids)
    hits = []
    for f in findings:
        suffix = _suffix(f)
        if suffix in identifiers or any(
            suffix == i or suffix.startswith(f"{i}-") or suffix.endswith(f"-{i}")
            for i in identifiers
        ):
            hits.append(f)
            continue
        refs = [r for r in f.packet.refs if r.kind == "transaction"]
        if not refs:
            continue
        overlap = sum(1 for r in refs if r.ref_id in identifiers)
        if overlap and overlap / len(refs) > DECOY_DOMINANCE:
            hits.append(f)
    return tuple(hits)


def run_bench(
    case: SeededCase,
    *,
    registry: RuleRegistry | None = None,
    policy: dict[str, Any] | None = None,
    only: Sequence[str] | None = None,
) -> BenchResult:
    """Run the control catalogue over a seeded case and score it."""
    started = time.perf_counter()
    ctx = RuleContext(
        ledger=case.ledger,
        period_start=case.period_start,
        period_end=case.period_end,
        fiscal_year_start=case.fiscal_year_start,
        statements=tuple(case.company.statements),
        policy=dict(policy or DEFAULT_POLICY),
    ).prepare()
    outcome = run_rules(ctx, registry=registry or REGISTRY, only=only)
    duration = (time.perf_counter() - started) * 1000

    findings = outcome.findings
    detections = tuple(Detection(error=e, findings=_match(findings, e)) for e in case.planted())
    decoy_hits = tuple(
        Detection(error=d, findings=_match_decoy(findings, d)) for d in case.decoys()
    )

    explained: set[str] = set()
    for e in case.planted():
        explained.update(f.finding_id for f in _explains(findings, e))
    for d in decoy_hits:
        explained.update(f.finding_id for f in d.findings)
    unexplained = tuple(f for f in findings if f.finding_id not in explained)

    return BenchResult(
        case_name=case.name,
        planted=case.planted(),
        decoys=case.decoys(),
        detections=detections,
        decoy_hits=decoy_hits,
        outcome=outcome,
        unexplained_findings=unexplained,
        duration_ms=duration,
        currency=case.ledger.currency,
    )
