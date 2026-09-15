"""The learning loop: outcomes in, better judgement out.

Every finding the operator accepts, dismisses or corrects is recorded against
a *pattern*, not a one-off id. Three dismissals of the same pattern on the same
client suppress it; one acceptance makes it a known pattern that lowers review
depth; a correction becomes a precedent. This is the mechanism by which ForgeOS
gets more competent on a client the longer it works with them, and it is a
file of decisions, not a model's memory, so it can be audited and reversed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..rules.base import Finding

__all__ = ["Outcome", "OutcomeLog", "LearningPolicy", "pattern_key", "apply_learning"]

Decision = Literal["accepted", "dismissed", "corrected", "deferred"]
SUPPRESS_AFTER = 3


def pattern_key(finding: Finding) -> str:
    """A stable key for 'this kind of finding on this counterparty or account'.

    The finding id is unique per run. The pattern is what recurs: the same rule
    on the same vendor, customer or account. Parties and accounts are picked out
    of the finding suffix; anything else collapses to the rule alone.
    """
    _, _, suffix = finding.finding_id.partition(":")
    tokens = suffix.replace("|", "-").split("-")
    for i, tok in enumerate(tokens):
        if tok in ("CUST", "VEND", "EMP", "JOB") and i + 1 < len(tokens):
            return f"{finding.rule_id}:{tok}-{tokens[i + 1]}"
    for ref in finding.packet.refs:
        if ref.kind == "account":
            return f"{finding.rule_id}:acct-{ref.ref_id}"
    if tokens and tokens[0].isdigit():
        return f"{finding.rule_id}:acct-{tokens[0]}"
    return finding.rule_id


@dataclass
class Outcome:
    finding_id: str
    rule_id: str
    pattern: str
    decision: Decision
    by: str
    reason: str = ""
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class OutcomeLog:
    client_id: str
    outcomes: list[Outcome] = field(default_factory=list)
    path: Path | None = None

    @classmethod
    def load(cls, client_id: str, directory: Path) -> OutcomeLog:
        path = Path(directory) / f"{client_id}.outcomes.json"
        if not path.exists():
            return cls(client_id=client_id, path=path)
        return cls(client_id=client_id, path=path, outcomes=[Outcome(**o) for o in json.loads(path.read_text())])

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(o) for o in self.outcomes], indent=2))

    def record(self, finding: Finding, decision: Decision, *, by: str, reason: str = "") -> Outcome:
        o = Outcome(finding.finding_id, finding.rule_id, pattern_key(finding), decision, by, reason)
        self.outcomes.append(o)
        return o

    def policy(self) -> LearningPolicy:
        return LearningPolicy.from_log(self)


@dataclass(frozen=True)
class LearningPolicy:
    suppressed: frozenset[str]
    known: frozenset[str]
    dismissal_counts: dict[str, int]
    acceptance_counts: dict[str, int]

    @classmethod
    def from_log(cls, log: OutcomeLog) -> LearningPolicy:
        dismissed: dict[str, int] = {}
        accepted: dict[str, int] = {}
        for o in log.outcomes:
            if o.decision == "dismissed":
                dismissed[o.pattern] = dismissed.get(o.pattern, 0) + 1
            elif o.decision in ("accepted", "corrected"):
                accepted[o.pattern] = accepted.get(o.pattern, 0) + 1
        # An acceptance after dismissals reopens the pattern: the operator changed
        # their mind, and the newer signal wins.
        suppressed = {p for p, n in dismissed.items() if n >= SUPPRESS_AFTER and accepted.get(p, 0) == 0}
        return cls(frozenset(suppressed), frozenset(accepted), dismissed, accepted)

    def is_suppressed(self, finding: Finding) -> bool:
        return pattern_key(finding) in self.suppressed

    def is_known(self, finding: Finding) -> bool:
        return pattern_key(finding) in self.known

    def to_dict(self) -> dict:
        return {"suppressed": sorted(self.suppressed), "known": sorted(self.known)}


def apply_learning(findings: Iterable[Finding], policy: LearningPolicy | None) -> tuple[list[Finding], list[Finding]]:
    """Split findings into (kept, suppressed). Critical findings are never suppressed."""
    if policy is None:
        return list(findings), []
    kept, dropped = [], []
    for f in findings:
        if f.severity.value != "critical" and policy.is_suppressed(f):
            dropped.append(f)
        else:
            kept.append(f)
    return kept, dropped
