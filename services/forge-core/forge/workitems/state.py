"""The work-item state machine.

Every finance task, from a coding suggestion to a covenant decision, is one work
item moving through one machine. Making the transitions explicit is what stops
"reviewed" from quietly meaning "a second model said it looked fine".

Two invariants are enforced here rather than left to convention:

* An item cannot reach APPROVED while any review note is unresolved.
* An item cannot reach ACTION_EXECUTED without an approval whose scope actually
  covers the action being taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..agents.contracts import AgentCall, CorrectionResponse, PreparerProposal, ReviewDecision
from ..canonical.enums import AgentRole, AutonomyLevel, WorkItemState
from ..evidence.packet import EvidencePacket
from ..rules.base import Finding
from .risk import ReviewPlan, RiskScore

__all__ = ["TransitionError", "AuditEvent", "WorkItem", "ALLOWED_TRANSITIONS"]


class TransitionError(RuntimeError):
    """Raised when a state change would break a control invariant."""


ALLOWED_TRANSITIONS: dict[WorkItemState, frozenset[WorkItemState]] = {
    WorkItemState.DRAFT: frozenset({WorkItemState.VALIDATING, WorkItemState.CLOSED}),
    WorkItemState.VALIDATING: frozenset(
        {WorkItemState.PREPARING, WorkItemState.VALIDATION_FAILED}
    ),
    WorkItemState.VALIDATION_FAILED: frozenset({WorkItemState.VALIDATING, WorkItemState.CLOSED}),
    WorkItemState.PREPARING: frozenset(
        {WorkItemState.AWAITING_REVIEW, WorkItemState.ESCALATED, WorkItemState.VALIDATION_FAILED}
    ),
    WorkItemState.AWAITING_REVIEW: frozenset(
        {
            WorkItemState.RETURNED_FOR_CORRECTION,
            WorkItemState.ESCALATED,
            WorkItemState.APPROVED,
            WorkItemState.AWAITING_HUMAN_APPROVAL,
            WorkItemState.REJECTED,
        }
    ),
    WorkItemState.RETURNED_FOR_CORRECTION: frozenset(
        {WorkItemState.CORRECTING, WorkItemState.REJECTED}
    ),
    WorkItemState.CORRECTING: frozenset(
        {WorkItemState.AWAITING_REVIEW, WorkItemState.ESCALATED}
    ),
    WorkItemState.ESCALATED: frozenset(
        {
            WorkItemState.AWAITING_REVIEW,
            WorkItemState.AWAITING_HUMAN_APPROVAL,
            WorkItemState.REJECTED,
        }
    ),
    WorkItemState.AWAITING_HUMAN_APPROVAL: frozenset(
        {WorkItemState.APPROVED, WorkItemState.REJECTED, WorkItemState.RETURNED_FOR_CORRECTION}
    ),
    WorkItemState.APPROVED: frozenset({WorkItemState.ACTION_PENDING, WorkItemState.CLOSED}),
    WorkItemState.ACTION_PENDING: frozenset(
        {WorkItemState.ACTION_EXECUTED, WorkItemState.ACTION_FAILED, WorkItemState.CLOSED}
    ),
    WorkItemState.ACTION_EXECUTED: frozenset(
        {WorkItemState.ACTION_VERIFIED, WorkItemState.ACTION_FAILED}
    ),
    WorkItemState.ACTION_VERIFIED: frozenset({WorkItemState.CLOSED}),
    WorkItemState.ACTION_FAILED: frozenset({WorkItemState.ESCALATED, WorkItemState.CLOSED}),
    WorkItemState.REJECTED: frozenset(),
    WorkItemState.CLOSED: frozenset(),
}

# Which approval scopes permit which actions. An approval of "analysis_only"
# never authorises a posting, however confident the chain was.
SCOPE_PERMITS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "analysis_only": frozenset({"no_action", "investigate", "request_document"}),
    "draft_action": frozenset(
        {"no_action", "investigate", "request_document", "draft_journal_entry", "draft_email"}
    ),
    "post_entry": frozenset(
        {
            "no_action", "investigate", "request_document", "draft_journal_entry",
            "draft_email", "correct_coding",
        }
    ),
    "send_communication": frozenset(
        {"no_action", "investigate", "request_document", "draft_email"}
    ),
    "make_payment": frozenset({"recover_payment", "escalate_to_owner"}),
    "file_return": frozenset({"remit_or_file", "escalate_to_owner"}),
}


@dataclass(frozen=True)
class AuditEvent:
    """One immutable entry in the work item's history.

    Constitution rule #8: every data change, calculation, agent decision,
    reviewer comment, approval, model version, action and outcome is logged.
    """

    at: datetime
    actor: str
    action: str
    detail: str
    from_state: WorkItemState | None = None
    to_state: WorkItemState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "detail": self.detail,
            "from": self.from_state.value if self.from_state else None,
            "to": self.to_state.value if self.to_state else None,
            "metadata": self.metadata,
        }


@dataclass
class WorkItem:
    """One unit of finance work moving through the review hierarchy."""

    work_item_id: str
    tenant_id: str
    entity_id: str
    objective: str
    finding: Finding
    packet: EvidencePacket
    risk: RiskScore
    plan: ReviewPlan
    period_start: date
    period_end: date
    state: WorkItemState = WorkItemState.DRAFT
    autonomy: AutonomyLevel = AutonomyLevel.A0_OBSERVE

    proposal: PreparerProposal | None = None
    reviews: list[ReviewDecision] = field(default_factory=list)
    corrections: list[CorrectionResponse] = field(default_factory=list)
    calls: list[AgentCall] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    final_scope: str = "none"
    rounds: int = 0
    max_rounds: int = 3

    # ---- state machine -------------------------------------------------

    def transition(
        self,
        to: WorkItemState,
        *,
        actor: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
    ) -> WorkItem:
        allowed = ALLOWED_TRANSITIONS[self.state]
        if to not in allowed:
            raise TransitionError(
                f"{self.work_item_id}: cannot move from {self.state.value} to {to.value}"
            )
        if to is WorkItemState.APPROVED and self.unresolved_notes:
            raise TransitionError(
                f"{self.work_item_id}: {len(self.unresolved_notes)} review note(s) are "
                "unresolved; an item cannot be approved over an open correction"
            )
        self.audit.append(
            AuditEvent(
                at=datetime.utcnow(),
                actor=actor,
                action="transition",
                detail=detail,
                from_state=self.state,
                to_state=to,
                metadata=metadata or {},
            )
        )
        self.state = to
        return self

    def record(self, actor: str, action: str, detail: str, **metadata: Any) -> None:
        self.audit.append(
            AuditEvent(
                at=datetime.utcnow(),
                actor=actor,
                action=action,
                detail=detail,
                from_state=self.state,
                to_state=self.state,
                metadata=metadata,
            )
        )

    # ---- review state --------------------------------------------------

    @property
    def unresolved_notes(self) -> list:
        notes = []
        for review in self.reviews:
            notes.extend(review.unresolved_notes)
        return notes

    @property
    def reviewers_completed(self) -> tuple[AgentRole, ...]:
        return tuple(r.role for r in self.reviews)

    @property
    def next_reviewer(self) -> AgentRole | None:
        done = set(self.reviewers_completed)
        for role in self.plan.reviewers:
            if role not in done:
                return role
        return None

    @property
    def chain_complete(self) -> bool:
        return self.next_reviewer is None and not self.unresolved_notes

    @property
    def is_approved(self) -> bool:
        return self.state in (
            WorkItemState.APPROVED,
            WorkItemState.ACTION_PENDING,
            WorkItemState.ACTION_EXECUTED,
            WorkItemState.ACTION_VERIFIED,
        )

    # ---- action gating -------------------------------------------------

    def may_execute(self, action_kind: str) -> tuple[bool, str]:
        """Whether the approved scope and the autonomy level permit this action."""
        if not self.is_approved:
            return False, f"item is in state {self.state.value}, not approved"
        permitted = SCOPE_PERMITS.get(self.final_scope, frozenset())
        if action_kind not in permitted:
            return (
                False,
                f"approved scope '{self.final_scope}' does not permit action '{action_kind}'",
            )
        if self.autonomy.level < 2 and action_kind not in (
            "no_action", "investigate", "request_document", "draft_journal_entry", "draft_email",
        ):
            return (
                False,
                f"autonomy level {self.autonomy.value} permits drafting only; a human must execute",
            )
        if self.plan.requires_human and self.autonomy.level < 4:
            return False, "this risk tier requires explicit human authorisation"
        return True, "permitted"

    # ---- cost ----------------------------------------------------------

    @property
    def cost_micros(self) -> int:
        return sum(c.cost_micros for c in self.calls)

    @property
    def tokens_used(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "tenant_id": self.tenant_id,
            "entity_id": self.entity_id,
            "objective": self.objective,
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "state": self.state.value,
            "autonomy": self.autonomy.value,
            "risk": self.risk.to_dict(),
            "review_plan": self.plan.to_dict(),
            "finding": self.finding.to_dict(),
            "proposal": self.proposal.model_dump(mode="json") if self.proposal else None,
            "reviews": [r.model_dump(mode="json") for r in self.reviews],
            "corrections": [c.model_dump(mode="json") for c in self.corrections],
            "rounds": self.rounds,
            "final_scope": self.final_scope,
            "unresolved_notes": len(self.unresolved_notes),
            "cost_micros": self.cost_micros,
            "tokens": self.tokens_used,
            "model_calls": [c.model_dump(mode="json") for c in self.calls],
            "audit": [e.to_dict() for e in self.audit],
        }
