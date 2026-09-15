"""The review hierarchy: routing, contracts, the correction loop and action gating."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from forge.agents.contracts import (
    CorrectionResponse,
    PreparerProposal,
    ProposedAction,
    ReviewDecision,
    ReviewNote,
)
from forge.agents.gateway import ModelGateway
from forge.canonical.enums import (
    AgentRole,
    AutonomyLevel,
    ReviewDecisionKind,
    RiskTier,
    WorkItemState,
)
from forge.pipeline import run_continuous_controller
from forge.workitems.state import TransitionError


class TestContracts:
    def _action(self):
        return ProposedAction(kind="investigate", summary="Look at the invoice.")

    def test_a_proposal_without_evidence_is_invalid(self):
        with pytest.raises(ValidationError):
            PreparerProposal(
                role=AgentRole.BOOKKEEPER, conclusion="It is fine.",
                confidence=Decimal("0.9"), confidence_reason="Looks right.",
                proposed_action=self._action(),
            )

    def test_a_return_without_correction_notes_is_invalid(self):
        with pytest.raises(ValidationError):
            ReviewDecision(
                role=AgentRole.CONTROLLER, decision=ReviewDecisionKind.RETURN,
                evidence_test_passed=False, control_test_passed=True,
                strongest_alternative="None considered.", residual_risk="Unknown.",
                approved_scope="none",
            )

    def test_an_escalation_must_state_why(self):
        with pytest.raises(ValidationError):
            ReviewDecision(
                role=AgentRole.CONTROLLER, decision=ReviewDecisionKind.ESCALATE,
                evidence_test_passed=True, control_test_passed=True,
                strongest_alternative="x", residual_risk="y", approved_scope="none",
            )

    def test_an_approval_must_state_its_scope(self):
        with pytest.raises(ValidationError):
            ReviewDecision(
                role=AgentRole.CONTROLLER, decision=ReviewDecisionKind.APPROVE,
                evidence_test_passed=True, control_test_passed=True,
                strongest_alternative="x", residual_risk="y", approved_scope="none",
            )

    def test_notes_must_be_answered_individually(self):
        notes = [
            ReviewNote(note_id="N1", defect="d1", kind="unsupported", required_correction="c1"),
            ReviewNote(note_id="N2", defect="d2", kind="incomplete", required_correction="c2"),
        ]
        proposal = PreparerProposal(
            role=AgentRole.BOOKKEEPER, conclusion="Revised.",
            supporting_evidence_ids=["TXN-1"], confidence=Decimal("0.8"),
            confidence_reason="Evidence is complete.", proposed_action=self._action(),
        )
        partial = CorrectionResponse(
            role=AgentRole.BOOKKEEPER, note_responses={"N1": "fixed"},
            revised_proposal=proposal,
        )
        assert not partial.covers(notes)
        assert partial.unanswered(notes) == ["N2"]


class TestRouting:
    def test_overdue_invoice_does_not_reach_the_cfo(self, clean_run):
        overdue = [i for i in clean_run.work_items if i.finding.rule_id == "FOS-R027"]
        assert overdue, "expected the clean book to carry overdue invoices"
        for item in overdue:
            assert item.risk.tier.level <= 3
            assert AgentRole.CFO not in item.plan.reviewers

    def test_a_preparer_never_reviews_its_own_work(self, clean_run):
        for item in clean_run.work_items:
            assert item.plan.preparer not in item.plan.reviewers

    def test_every_tier_records_its_reasons(self, clean_run):
        for item in clean_run.work_items:
            assert item.risk.factors
            for factor in item.risk.factors:
                assert factor.reason

    def test_tier_can_be_escalated_but_not_lowered(self, clean_run):
        item = clean_run.work_items[0]
        raised = item.risk.escalate_to(RiskTier.R4, "controller judgement")
        assert raised.tier is RiskTier.R4
        lowered = raised.escalate_to(RiskTier.R1, "attempt to downgrade")
        assert lowered.tier is RiskTier.R4

    def test_material_tiers_get_more_reviewers_than_routine_ones(self, gate_passing_case):
        run = run_continuous_controller(
            gate_passing_case.ledger,
            period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
        )
        by_tier: dict[int, list[int]] = {}
        for item in run.work_items:
            by_tier.setdefault(item.risk.tier.level, []).append(len(item.plan.reviewers))
        assert max(by_tier) >= 3
        for lower, higher in zip(sorted(by_tier), sorted(by_tier)[1:], strict=False):
            assert min(by_tier[higher]) >= min(by_tier[lower])


class TestStateMachine:
    def test_illegal_transitions_are_refused(self, clean_run):
        item = clean_run.work_items[0]
        with pytest.raises(TransitionError):
            item.transition(WorkItemState.APPROVED, actor="test", detail="skip the chain")

    def test_cannot_approve_over_an_unresolved_note(self, clean_run):
        item = clean_run.work_items[0]
        item.transition(WorkItemState.VALIDATING, actor="t", detail="")
        item.transition(WorkItemState.PREPARING, actor="t", detail="")
        item.transition(WorkItemState.AWAITING_REVIEW, actor="t", detail="")
        item.reviews.append(
            ReviewDecision(
                role=AgentRole.ACCOUNTING_MANAGER, decision=ReviewDecisionKind.RETURN,
                evidence_test_passed=False, control_test_passed=True,
                strongest_alternative="x", residual_risk="y", approved_scope="none",
                notes=[
                    ReviewNote(
                        note_id="N1", defect="Unsupported", kind="unsupported",
                        required_correction="Attach the invoice",
                    )
                ],
            )
        )
        with pytest.raises(TransitionError):
            item.transition(WorkItemState.APPROVED, actor="t", detail="force")

    def test_approved_analysis_does_not_authorise_a_payment(self, gate_passing_case, offline_gateway):
        run = run_continuous_controller(
            gate_passing_case.ledger, period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
            gateway=offline_gateway, review=True, review_limit=6,
        )
        approved = [i for i in run.work_items if i.state is WorkItemState.APPROVED]
        assert approved
        item = approved[0]
        assert item.final_scope == "analysis_only"
        allowed, _ = item.may_execute("investigate")
        assert allowed
        refused, why = item.may_execute("recover_payment")
        assert not refused and "scope" in why

    def test_observe_autonomy_never_executes(self, clean_run):
        item = clean_run.work_items[0]
        assert item.autonomy is AutonomyLevel.A0_OBSERVE
        allowed, why = item.may_execute("draft_journal_entry")
        assert not allowed


class TestReviewLoop:
    def test_the_data_gate_blocks_model_reasoning(self, seeded_case, offline_gateway):
        """The seeded case contains an unbalanced entry, so nothing may be reasoned over."""
        run = run_continuous_controller(
            seeded_case.ledger, period_start=seeded_case.period_start,
            period_end=seeded_case.period_end, statements=seeded_case.company.statements,
            gateway=offline_gateway, review=True,
        )
        assert not run.passed_data_gate
        assert all(not item.calls for item in run.work_items)

    def test_the_chain_runs_and_is_fully_audited(self, gate_passing_case, offline_gateway):
        run = run_continuous_controller(
            gate_passing_case.ledger, period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
            gateway=offline_gateway, review=True, review_limit=6,
        )
        assert run.passed_data_gate and run.reviewed
        reviewed = [i for i in run.work_items if i.calls]
        assert reviewed
        for item in reviewed:
            assert item.proposal is not None
            assert item.audit
            assert item.audit[0].to_state is WorkItemState.VALIDATING
            assert item.state in (
                WorkItemState.APPROVED, WorkItemState.AWAITING_HUMAN_APPROVAL,
                WorkItemState.ESCALATED,
            )

    def test_highest_tier_items_stop_for_a_human(self, gate_passing_case, offline_gateway):
        run = run_continuous_controller(
            gate_passing_case.ledger, period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
            gateway=offline_gateway, review=True, review_limit=6,
        )
        r4 = [i for i in run.work_items if i.risk.tier is RiskTier.R4 and i.calls]
        assert r4
        for item in r4:
            assert item.state is WorkItemState.AWAITING_HUMAN_APPROVAL

    def test_lack_of_model_independence_is_recorded_not_hidden(
        self, gate_passing_case, offline_gateway
    ):
        run = run_continuous_controller(
            gate_passing_case.ledger, period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
            gateway=offline_gateway, review=True, review_limit=3,
        )
        reviewed = [i for i in run.work_items if i.calls]
        notices = [
            e for item in reviewed for e in item.audit
            if e.action == "independence_unavailable"
        ]
        assert notices, (
            "with a single model family configured, the system must say the review "
            "was not independent rather than implying it was"
        )

    def test_cost_is_tracked_per_call(self, gate_passing_case, offline_gateway):
        run_continuous_controller(
            gate_passing_case.ledger, period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
            gateway=offline_gateway, review=True, review_limit=3,
        )
        summary = offline_gateway.cost_summary()
        assert summary["calls"] > 0
        assert summary["failed_calls"] == 0
        assert summary["tokens"] > 0
        assert set(summary["by_role"])


class TestGateway:
    def test_routing_escalates_with_risk(self):
        from forge.agents.gateway import DEFAULT_MODELS, OfflineDeterministicProvider

        gateway = ModelGateway(
            providers={"offline": OfflineDeterministicProvider(), "anthropic": None},
            models=DEFAULT_MODELS,
            default_provider="anthropic",
        )
        gateway.providers = {"anthropic": OfflineDeterministicProvider()}
        cheap = gateway.select(tier=RiskTier.R1, role=AgentRole.BOOKKEEPER)
        dear = gateway.select(tier=RiskTier.R4, role=AgentRole.CFO)
        assert dear.input_cost_per_mtok >= cheap.input_cost_per_mtok

    def test_invalid_responses_are_retried_then_fail_loudly(self):
        from forge.agents.gateway import GatewayError, ModelResponse, Provider

        class Broken(Provider):
            name = "broken"

            def __init__(self):
                self.attempts = 0

            def complete(self, **kwargs):
                self.attempts += 1
                return ModelResponse(text='{"nope": true}')

        broken = Broken()
        gateway = ModelGateway(providers={"offline": broken})
        with pytest.raises(GatewayError):
            gateway.call(
                role=AgentRole.BOOKKEEPER, tier=RiskTier.R1, system="s", user="u",
                response_model=PreparerProposal, packet_checksum="abc",
            )
        assert broken.attempts == gateway.max_attempts
        assert gateway.calls and not gateway.calls[-1].succeeded
