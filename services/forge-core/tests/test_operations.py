"""Tax, close, learning, drafts, growth and firm operations: the layer that turns
one control run into a finance team's day."""

from __future__ import annotations

from datetime import date

import pytest

from forge.actions import draft_for
from forge.canonical.enums import AgentRole, AutonomyLevel, Severity, WorkItemState
from forge.cfo import owner_brief
from forge.clients import ClientProfile
from forge.close import build_close_checklist
from forge.growth import (
    STAGES,
    Prospect,
    SalesPipeline,
    build_proposal,
    diagnostic_fee,
    recommend_engagement,
    render_proposal,
)
from forge.learning import SUPPRESS_AFTER, OutcomeLog, apply_learning, pattern_key
from forge.money import Money
from forge.operations import build_firm_queue, daily_brief, job_plan, run_client
from forge.pipeline import run_continuous_controller
from forge.rules import REGISTRY
from forge.tax import (
    corporate_tax_provision,
    hst_filing_frequency,
    sales_tax_return,
    slip_obligations,
    tax_calendar,
)
from forge.workitems.risk import PREPARERS, SPECIALIST_FOR
from tests.conftest import PERIOD_END, PERIOD_START


def _profile(**kw) -> ClientProfile:
    base = {"client_id": "C1", "business_name": "Test Co", "province": "ON", "naics_code": "238210",
            "annual_revenue_estimate": Money.from_decimal("2400000.00"), "owner_name": "Amro E",
            "services": ("bookkeeping", "payroll", "tax", "cfo_advisory"), "uses_subcontractors": True,
            "employees_full_time": 8}
    base.update(kw)
    return ClientProfile(**base)


@pytest.fixture(scope="module")
def client_run(clean_company):
    return run_client(_profile(), clean_company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                      statements=clean_company.statements)


@pytest.fixture()
def seeded_run(seeded_case):
    return run_continuous_controller(seeded_case.company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                                     statements=seeded_case.company.statements, enforce_data_gate=False)


class TestRolesAreComplete:
    def test_every_agent_role_has_a_mandate(self):
        from forge.agents.roles import ROLE_MANDATES
        missing = [r for r in AgentRole if r not in ROLE_MANDATES and r is not AgentRole.HUMAN]
        assert not missing, missing

    def test_every_control_category_has_a_preparer(self):
        categories = {r.category for r in REGISTRY}
        assert not categories - set(PREPARERS), categories - set(PREPARERS)
        # Specialists only exist for domains the generalist chain does not cover.
        assert set(SPECIALIST_FOR.values()) >= {AgentRole.TAX_SPECIALIST, AgentRole.PAYROLL_SPECIALIST,
                                                AgentRole.TREASURY_SPECIALIST, AgentRole.FINANCE_BUSINESS_PARTNER,
                                                AgentRole.FPA_MANAGER}


class TestSalesTaxReturn:
    def test_net_tax_is_collected_less_credits(self, clean_context):
        r = sales_tax_return(clean_context)
        assert r.line_109_net_tax == r.line_103_tax_collected - r.line_106_input_tax_credits
        assert r.line_113_balance == r.line_109_net_tax - r.line_110_instalments
        assert r.line_101_sales.minor_units > 0 and r.sales_transaction_count > 0

    def test_collected_tax_is_consistent_with_the_provincial_rate(self, clean_context):
        r = sales_tax_return(clean_context)
        implied = r.line_103_tax_collected.minor_units / r.line_101_sales.minor_units
        assert abs(implied - 0.13) < 0.005

    def test_return_is_reconciled_to_the_ledger_liability(self, clean_context):
        r = sales_tax_return(clean_context)
        assert r.ledger_liability_closing.minor_units != 0


class TestTaxCalendar:
    def test_filing_frequency_follows_taxable_supplies(self):
        assert hst_filing_frequency(Money.from_decimal("900000.00")) == "annual"
        assert hst_filing_frequency(Money.from_decimal("2000000.00")) == "quarterly"
        assert hst_filing_frequency(Money.from_decimal("7000000.00")) == "monthly"
        assert hst_filing_frequency(None) == "quarterly"

    def test_calendar_carries_payroll_slips_and_corporate_dates(self):
        kinds = {d.kind for d in tax_calendar(_profile(wsib_number="123"), year=2026)}
        assert {"sales_tax_return", "payroll_remittance", "t4_filing", "t5018", "wsib_premium", "t2_balance_due", "t2_filing"} <= kinds

    def test_non_calendar_year_end_moves_the_corporate_dates(self):
        cal = tax_calendar(_profile(fiscal_year_end_month=9, fiscal_year_end_day=30), year=2026)
        balance = next(d for d in cal if d.kind == "t2_balance_due")
        filing = next(d for d in cal if d.kind == "t2_filing")
        assert balance.due_on == date(2026, 12, 31)
        assert filing.due_on == date(2027, 3, 31)

    def test_dates_are_flagged_confirm_where_the_authority_assigns_them(self):
        cal = tax_calendar(_profile(), year=2026)
        assert any(d.confirm for d in cal if d.kind == "sales_tax_return")

    def test_today_keeps_sixty_days_of_history_for_missed_filings(self):
        cal = tax_calendar(_profile(), year=2026, today=date(2026, 6, 30))
        assert all(d.due_on >= date(2026, 5, 1) for d in cal)
        assert any(d.due_on < date(2026, 6, 30) for d in cal)


class TestCorporateProvision:
    def test_meals_and_depreciation_are_added_back(self, clean_context):
        p = corporate_tax_provision(clean_context)
        assert p.taxable_income_estimate == p.accounting_income + p.add_back_meals + p.add_back_depreciation
        assert p.add_back_depreciation.minor_units > 0

    def test_small_business_rate_applies_below_the_limit(self, clean_context):
        p = corporate_tax_provision(clean_context)
        assert p.general_portion.is_zero
        expected = p.small_business_portion.to_decimal() * (p.rates.federal_small_business + p.rates.provincial_small_business)
        assert abs(p.tax_small_business.to_decimal() - expected) < 1

    def test_a_cca_claim_reduces_the_estimate(self, clean_context):
        base = corporate_tax_provision(clean_context)
        with_cca = corporate_tax_provision(clean_context, cca_claim=Money.from_decimal("10000.00"))
        assert with_cca.taxable_income_estimate == base.taxable_income_estimate - Money.from_decimal("10000.00")


class TestSlips:
    def test_construction_subcontractors_over_threshold_get_t5018(self, clean_context):
        slips = slip_obligations(clean_context, year=2026, construction=True)
        assert slips and all(s.slip == "T5018" for s in slips)
        assert all(s.amount_paid >= Money.from_decimal("500.00") for s in slips)

    def test_non_construction_gets_t4a(self, clean_context):
        slips = slip_obligations(clean_context, year=2026, construction=False)
        assert slips and all(s.slip == "T4A" for s in slips)


class TestCloseChecklist:
    def test_clean_books_close_with_open_review_tasks_only(self, clean_context, clean_run):
        cl = build_close_checklist(clean_context, clean_run.findings, _profile())
        assert cl.tasks[0].task_id == "tie_out" and cl.tasks[0].status == "done"
        assert any(t.task_id.startswith("reconcile_") and t.status == "done" for t in cl.tasks)
        assert cl.tasks[-1].task_id == "lock_period"
        assert 0 < cl.percent_complete <= 100

    def test_findings_block_their_tasks(self, seeded_case, seeded_run):
        ctx = seeded_run.ctx
        cl = build_close_checklist(ctx, seeded_run.findings, _profile())
        by_id = {t.task_id: t for t in cl.tasks}
        assert by_id["depreciation"].status == "open"
        assert "FOS-R042" in by_id["depreciation"].related_rules
        assert by_id["tie_out"].status == "blocked"
        assert not cl.can_lock

    def test_deferred_revenue_is_not_applicable_for_a_contractor(self, clean_context, clean_run):
        cl = build_close_checklist(clean_context, clean_run.findings, _profile(naics_code="238210", seasonal=False))
        assert next(t for t in cl.tasks if t.task_id == "deferred_revenue").status == "not_applicable"


class TestLearningLoop:
    def test_pattern_key_is_the_rule_plus_the_counterparty(self, seeded_run):
        f = next(f for f in seeded_run.findings if "CUST-" in f.finding_id or "VEND-" in f.finding_id)
        key = pattern_key(f)
        assert key.startswith(f.rule_id + ":") and ("CUST-" in key or "VEND-" in key)

    def test_three_dismissals_suppress_and_an_acceptance_reopens(self, seeded_run):
        f = next(f for f in seeded_run.findings if f.severity is not Severity.CRITICAL)
        log = OutcomeLog(client_id="C1")
        for _ in range(SUPPRESS_AFTER - 1):
            log.record(f, "dismissed", by="amro")
        assert not log.policy().is_suppressed(f)
        log.record(f, "dismissed", by="amro")
        assert log.policy().is_suppressed(f)
        log.record(f, "accepted", by="amro")
        assert not log.policy().is_suppressed(f)
        assert log.policy().is_known(f)

    def test_critical_findings_are_never_suppressed(self, seeded_run):
        crit = next(f for f in seeded_run.findings if f.severity is Severity.CRITICAL)
        log = OutcomeLog(client_id="C1")
        for _ in range(SUPPRESS_AFTER + 2):
            log.record(crit, "dismissed", by="amro")
        kept, suppressed = apply_learning(seeded_run.findings, log.policy())
        assert crit in kept and crit not in suppressed

    def test_suppressed_findings_are_recorded_on_the_run(self, seeded_case):
        first = run_continuous_controller(seeded_case.company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                                          statements=seeded_case.company.statements, enforce_data_gate=False)
        f = next(f for f in first.findings if f.severity is Severity.MEDIUM)
        log = OutcomeLog(client_id="C1")
        for _ in range(SUPPRESS_AFTER):
            log.record(f, "dismissed", by="amro")
        second = run_continuous_controller(seeded_case.company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                                           statements=seeded_case.company.statements, enforce_data_gate=False,
                                           learning=log.policy())
        assert second.suppressed and len(second.findings) < len(first.findings)
        assert second.summary()["suppressed_by_learning"] == len(second.suppressed)

    def test_known_patterns_lower_novelty_not_severity(self, seeded_case):
        first = run_continuous_controller(seeded_case.company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                                          statements=seeded_case.company.statements, enforce_data_gate=False)
        item = next(i for i in first.work_items if i.finding.severity is Severity.HIGH)
        log = OutcomeLog(client_id="C1")
        log.record(item.finding, "accepted", by="amro")
        second = run_continuous_controller(seeded_case.company.ledger, period_start=PERIOD_START, period_end=PERIOD_END,
                                           statements=seeded_case.company.statements, enforce_data_gate=False,
                                           learning=log.policy())
        again = next(i for i in second.work_items if i.finding.finding_id == item.finding.finding_id)
        assert again.finding.severity == item.finding.severity
        def novelty(i):
            return sum(f.weight for f in i.risk.factors if f.name == "novelty")
        assert novelty(again) < novelty(item)

    def test_log_round_trips_through_disk(self, seeded_run, tmp_path):
        f = seeded_run.findings[0]
        log = OutcomeLog.load("C1", tmp_path)
        log.record(f, "dismissed", by="amro", reason="known")
        log.save()
        again = OutcomeLog.load("C1", tmp_path)
        assert len(again.outcomes) == 1 and again.outcomes[0].pattern == pattern_key(f)


class TestDraftActions:
    def test_reversal_draft_is_balanced_and_gated(self, seeded_case, seeded_run):
        item = next(i for i in seeded_run.work_items if i.finding.rule_id == "FOS-R044")
        d = draft_for(item, seeded_case.company.ledger)
        assert d.kind == "draft_journal_entry" and len(d.lines) >= 2
        debits = sum(float(ln["amount"]) for ln in d.lines if ln["side"] == "debit")
        credits = sum(float(ln["amount"]) for ln in d.lines if ln["side"] == "credit")
        assert abs(debits - credits) < 0.005
        assert not d.authorised_now and "not approved" in d.reason_not_authorised

    def test_depreciation_draft_uses_the_proven_charge(self, seeded_case, seeded_run):
        item = next(i for i in seeded_run.work_items if i.finding.rule_id == "FOS-R042")
        d = draft_for(item, seeded_case.company.ledger)
        assert d.kind == "draft_journal_entry"
        expected = next(c for c in item.packet.calculations if c.name == "expected charge").result
        assert d.lines[0]["amount"] == str(expected.to_decimal())

    def test_approved_draft_scope_at_a1_authorises_a_draft_only(self, seeded_case, seeded_run):
        item = next(i for i in seeded_run.work_items if i.finding.rule_id == "FOS-R044")
        item.autonomy = AutonomyLevel.A1_DRAFT
        item.final_scope = "draft_action"
        for s in (WorkItemState.VALIDATING, WorkItemState.PREPARING, WorkItemState.AWAITING_REVIEW, WorkItemState.APPROVED):
            item.transition(s, actor="t", detail="")
        d = draft_for(item, seeded_case.company.ledger)
        assert d.authorised_now is (not item.plan.requires_human)
        ok, why = item.may_execute("correct_coding")
        assert not ok and "scope" in why

    def test_overdue_receivable_becomes_a_collection_email(self, seeded_case, seeded_run):
        item = next(i for i in seeded_run.work_items if i.finding.rule_id == "FOS-R027")
        d = draft_for(item, seeded_case.company.ledger)
        assert d.kind == "draft_email" and d.body


class TestGrowthEngine:
    def test_pricing_scales_with_services_size_and_cleanup(self):
        small = recommend_engagement(_profile(services=("bookkeeping",), months_behind=0))
        full = recommend_engagement(_profile())
        behind = recommend_engagement(_profile(months_behind=6))
        assert full.monthly_fee > small.monthly_fee
        assert behind.cleanup_fee == Money.from_decimal("2700.00")
        assert full.annual_value == full.monthly_fee.scale(12)
        assert diagnostic_fee(_profile()).minor_units > 0

    def test_proposal_reports_the_real_control_count(self, client_run):
        p = build_proposal(_profile(), client_run.run, today=PERIOD_END)
        text = render_proposal(p, firm="Profit Forge", sender="Amro")
        assert f"{p.controls_run} controls" in text and str(len(REGISTRY)) in text
        assert p.engagement.monthly_fee.format() in text

    def test_pipeline_stages_and_persistence(self, tmp_path):
        sp = SalesPipeline.load(tmp_path / "pipeline.json")
        sp.add(Prospect(prospect_id="P1", business_name="Acme"))
        sp.add(Prospect(prospect_id="P2", business_name="Beta"))
        sp.prospects["P1"].move("diagnostic_booked")
        sp.prospects["P2"].move("lost")
        with pytest.raises(ValueError):
            sp.prospects["P1"].move("closed")
        sp.save()
        again = SalesPipeline.load(tmp_path / "pipeline.json")
        assert again.funnel()["diagnostic_booked"] == 1 and again.funnel()["lost"] == 1
        assert set(again.funnel()) == set(STAGES)
        assert again.due_today(date(2099, 1, 1)) == [again.prospects["P1"]]


class TestFirmOperations:
    def test_client_run_carries_every_workflow(self, client_run):
        s = client_run.summary()
        assert s["passed_data_gate"] and s["health"]["score"] > 0
        assert client_run.sales_tax is not None and client_run.provision is not None
        assert client_run.close.tasks and client_run.deadlines and client_run.drafts
        assert s["cost_micros"] == 0

    def test_job_plan_covers_daily_monthly_and_annual_work(self):
        plan = job_plan(_profile(), today=date(2026, 6, 15))
        jobs = {j.job: j for j in plan}
        assert {"continuous_controller", "month_end_close", "sales_tax_return", "payroll_check", "cash_forecast", "year_end_package"} <= set(jobs)
        assert jobs["continuous_controller"].cadence == "daily"
        assert plan == sorted(plan, key=lambda j: j.next_due)

    def test_job_plan_respects_services(self):
        jobs = {j.job for j in job_plan(_profile(services=("bookkeeping",)), today=date(2026, 6, 15))}
        assert "payroll_check" not in jobs

    def test_firm_queue_orders_by_priority_then_value(self, client_run):
        q = build_firm_queue([client_run], as_of=PERIOD_END)
        priorities = [i.priority for i in q.items]
        assert priorities == sorted(priorities)
        assert q.clients == 1 and q.total_recoverable.minor_units > 0
        cash = [i for i in q.items if i.kind == "cash"]
        assert cash and cash[0].priority == 2

    def test_daily_brief_and_owner_brief_read_as_prose(self, client_run):
        q = build_firm_queue([client_run], as_of=PERIOD_END)
        brief = daily_brief(q, [client_run])
        assert brief.startswith("Daily brief, 2026-06-30") and "Test Co" in brief
        ob = owner_brief(client_run)
        assert ob.startswith("Test Co: the five things that matter")
        assert ob.count("\n\n") >= 4
