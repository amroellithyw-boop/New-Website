"""Structured contracts for preparers and reviewers.

Constitution rule #4: a reviewer does not merely comment. It must reach a
decision, identify specific defects, and state what evidence is missing. That is
only enforceable if the output is a *schema*, not prose, so every agent in
ForgeOS returns one of the models below and a response that does not validate is
a failed call rather than a soft warning.

Note on review notes: each is atomic and carries its own resolution state. A
preparer cannot respond to a returned item by regenerating the whole answer; it
must answer each note individually, and the same reviewer then re-checks that
the notes were actually addressed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..canonical.enums import AgentRole, ReviewDecisionKind

__all__ = [
    "ProposedAction",
    "PreparerProposal",
    "ReviewNote",
    "ReviewDecision",
    "CorrectionResponse",
    "AgentCall",
]


class ProposedAction(BaseModel):
    """What the preparer thinks should happen. Never executed without approval."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "no_action",
        "investigate",
        "request_document",
        "draft_journal_entry",
        "draft_email",
        "correct_coding",
        "recover_payment",
        "remit_or_file",
        "escalate_to_owner",
    ]
    summary: str = Field(min_length=1, max_length=600)
    reversible: bool = True
    requires_human_approval: bool = True
    estimated_value: str | None = Field(
        default=None, description="Decimal string in the entity currency, if quantifiable"
    )


class PreparerProposal(BaseModel):
    """A preparer's structured conclusion about one work item."""

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    conclusion: str = Field(min_length=1, max_length=2000)
    supporting_evidence_ids: list[str] = Field(
        default_factory=list,
        validate_default=True,
        description="Evidence ref ids from the packet that support the conclusion",
    )
    calculations_relied_on: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(
        default_factory=list,
        description="Other readings of the same evidence that were considered",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="What would have to be obtained to reach a firm conclusion",
    )
    confidence: Decimal = Field(ge=0, le=1)
    confidence_reason: str = Field(min_length=1, max_length=600)
    proposed_action: ProposedAction

    @field_validator("supporting_evidence_ids")
    @classmethod
    def _require_evidence(cls, value: list[str]) -> list[str]:
        # Constitution rule #1. A proposal with no evidence reference is not a
        # weak proposal; it is an invalid one.
        if not value:
            raise ValueError("a proposal must cite at least one evidence reference")
        return value

    @property
    def is_uncertain(self) -> bool:
        return self.confidence < Decimal("0.6") or bool(self.missing_evidence)


class ReviewNote(BaseModel):
    """One atomic, individually resolvable review comment."""

    model_config = ConfigDict(extra="forbid")

    note_id: str
    defect: str = Field(min_length=1, max_length=1000)
    kind: Literal[
        "unsupported",
        "incorrect",
        "incomplete",
        "inconsistent",
        "policy",
        "risk",
        "presentation",
    ]
    required_correction: str = Field(min_length=1, max_length=1000)
    expected_evidence: str | None = None
    resolved: bool = False
    resolution: str | None = None
    resolved_at: datetime | None = None

    def resolve(self, resolution: str) -> ReviewNote:
        return self.model_copy(
            update={
                "resolved": True,
                "resolution": resolution,
                "resolved_at": datetime.utcnow(),
            }
        )


class ReviewDecision(BaseModel):
    """A reviewer's structured verdict. Every field is mandatory by design."""

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    decision: ReviewDecisionKind
    evidence_test_passed: bool = Field(
        description="Whether every material assertion traces to source data or a calculation"
    )
    control_test_passed: bool = Field(
        description="Whether policy, period, entity, classification, cutoff and "
        "authorisation were checked and hold"
    )
    strongest_alternative: str = Field(
        min_length=1,
        max_length=1000,
        description="The best competing explanation, and why it is accepted or rejected",
    )
    notes: list[ReviewNote] = Field(default_factory=list)
    residual_risk: str = Field(min_length=1, max_length=1000)
    approved_scope: Literal[
        "none", "analysis_only", "draft_action", "post_entry", "send_communication",
        "make_payment", "file_return",
    ]
    escalate_to: AgentRole | None = None
    escalation_reason: str | None = None

    @field_validator("notes")
    @classmethod
    def _return_needs_notes(cls, value: list[ReviewNote], info) -> list[ReviewNote]:
        return value

    def model_post_init(self, _context: Any) -> None:
        if self.decision is ReviewDecisionKind.RETURN and not self.notes:
            raise ValueError("a RETURN decision must carry at least one correction note")
        if self.decision is ReviewDecisionKind.ESCALATE and not self.escalation_reason:
            raise ValueError("an ESCALATE decision must state why")
        if self.decision is ReviewDecisionKind.APPROVE and self.approved_scope == "none":
            raise ValueError("an APPROVE decision must state what is approved")

    @property
    def unresolved_notes(self) -> list[ReviewNote]:
        return [n for n in self.notes if not n.resolved]

    @property
    def blocks_advancement(self) -> bool:
        return (
            self.decision is not ReviewDecisionKind.APPROVE
            or bool(self.unresolved_notes)
        )


class CorrectionResponse(BaseModel):
    """A preparer's answer to a returned item, note by note."""

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    note_responses: dict[str, str] = Field(
        description="note_id -> what was changed, or why the note is not accepted"
    )
    revised_proposal: PreparerProposal

    def covers(self, notes: list[ReviewNote]) -> bool:
        """Every note must be answered individually before the item can advance."""
        return all(n.note_id in self.note_responses for n in notes)

    def unanswered(self, notes: list[ReviewNote]) -> list[str]:
        return [n.note_id for n in notes if n.note_id not in self.note_responses]


class AgentCall(BaseModel):
    """Reproducibility metadata for one model invocation."""

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    provider: str
    model: str
    prompt_version: str
    packet_checksum: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_micros: int = 0
    """Cost in millionths of a currency unit, kept as an integer for the same
    reason money is: fractions of a cent accumulate across millions of calls."""
    succeeded: bool = True
    error: str | None = None
    called_at: datetime = Field(default_factory=datetime.utcnow)
