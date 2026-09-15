"""The client-facing review must be accurate, traceable and free of leakage."""

from __future__ import annotations

import json

from forge.pipeline import run_continuous_controller
from forge.report import render_diagnostic, summary_sentence, write_evidence_bundle


def test_report_renders_and_names_the_client(clean_run):
    html = render_diagnostic(clean_run)
    assert clean_run.entity_name in html
    assert "<!doctype html>" in html.lower()
    assert len(html) > 5000


def test_report_states_the_tie_out_result(clean_run):
    html = render_diagnostic(clean_run)
    assert "The books tie out." in html


def test_failed_data_gate_leads_the_report(seeded_case):
    run = run_continuous_controller(
        seeded_case.ledger, period_start=seeded_case.period_start,
        period_end=seeded_case.period_end, statements=seeded_case.company.statements,
    )
    assert not run.passed_data_gate
    html = render_diagnostic(run)
    assert "do not tie out" in html
    assert "Read this first" in html


def test_every_finding_shows_its_calculations(clean_run):
    html = render_diagnostic(clean_run)
    for finding in clean_run.findings:
        assert finding.title in html
        for calc in finding.packet.calculations[:1]:
            assert calc.name in html


def test_report_does_not_leak_internal_identifiers_as_prose(clean_run):
    """Work item ids and packet checksums belong in the bundle, not the client copy."""
    html = render_diagnostic(clean_run)
    assert "WI-2026" not in html
    assert "packet_id" not in html


def test_summary_leads_with_what_changes_the_readers_next_action(clean_run):
    sentence = summary_sentence(clean_run)
    assert sentence.startswith("The books tie out")
    assert len(sentence) < 600


def test_summary_leads_with_the_failure_when_books_do_not_balance(seeded_case):
    run = run_continuous_controller(
        seeded_case.ledger, period_start=seeded_case.period_start,
        period_end=seeded_case.period_end, statements=seeded_case.company.statements,
    )
    assert summary_sentence(run).startswith("The accounting records do not")


def test_recoverable_cash_excludes_unrelated_exposure(clean_run):
    """Adding a covenant balance to a duplicate payment produces a meaningless number."""
    assert clean_run.recoverable_cash <= clean_run.total_exposure


def test_evidence_bundle_is_valid_json_and_complete(clean_run, tmp_path):
    path = write_evidence_bundle(clean_run, tmp_path / "evidence.json")
    payload = json.loads(path.read_text())
    assert payload["summary"]["entity"] == clean_run.entity_name
    assert len(payload["work_items"]) == len(clean_run.work_items)
    for item in payload["work_items"]:
        assert item["risk"]["factors"]
        assert item["finding"]["evidence"]["calculations"]


def test_report_has_no_unrendered_template_syntax(clean_run):
    html = render_diagnostic(clean_run)
    assert "{{" not in html and "{%" not in html
