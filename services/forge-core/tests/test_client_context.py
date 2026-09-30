"""Every client document and reply lands in that client's context, and future
engagements of any kind set themselves up from the letter."""

from __future__ import annotations

import json
from datetime import date

import pytest

from forge.clients import ClientProfile
from forge.clients.context import ClientContext, _set_path
from forge.documents.intake import (
    classify,
    deterministic_answers,
    deterministic_facts,
    propose_facts_updates,
)
from forge.engagements import (
    ENGAGEMENT_TYPES,
    EngagementRecord,
    detect_types,
    parse_engagement_letter,
)
from forge.money import Money
from forge.operations import run_client
from forge.realestate.facts import EngagementFacts

TODAY = date(2027, 3, 3)

LETTER = """Example Firm | Accounting · Tax · Bookkeeping
ENGAGEMENT LETTER
Client Jane Example
Co-owner John Example
Property 12 Sample Road, Toronto, Ontario
Project New fourplex plus detached garden suite, intended for long-term residential rental
1. Purpose
This engagement is between Jane Example (the "Client") and Example Firm.
Signing this letter authorizes Phase 1 only. Phase 2 may begin after the Client approves the monthly start date in writing.
March 3, 2027
2. Services and fees
PHASE 1 Canadian Tax, Ownership + GST/HST Action Plan
• Review the Client's Canadian departure/residency facts and later filing history.
• Assess the fourplex and detached garden suite separately for the applicable PBRH, NRRP and Ontario rental rebate pathways,
including timing, self-supply considerations and required support.
• Set up the construction accounting structure and deliver a written action plan.
$1,500 + HST
PHASE 2 Construction Accounting + Monthly Owner Finance Pack
• Maintain monthly project books and reconcile the project bank and construction-loan accounts.
• Provide a monthly Owner Finance Pack showing cost to date, budget versus actual, financing and draws.
PHASE 3 Completion, Self-Supply + Rental Rebate Filing
• Calculate the applicable GST/HST self-supply and rental rebate amounts and prepare the federal and Ontario forms.
$2,200 + HST
$600/month
+ HST
3. Fees, invoicing and payment
Additional services will be billed at $200/hour plus applicable HST.
4. Current project assumptions
• The Client lives in Italy and moved from Canada in July 2020.
• The property was purchased in 2018 for approximately $690,000.
5. Client responsibilities
• Provide complete, accurate and timely information.
7. Professional limitations and excluded services
• This is not an audit, review, compilation or other assurance engagement.
8. Acceptance
By signing below, the Client confirms acceptance.
"""

BOOKKEEPING_LETTER = """ENGAGEMENT LETTER
Client Acme Paving Inc
Business Acme Paving Inc, Hamilton, Ontario
1. Purpose
Signing this letter authorizes Phases 1 and 2.
2. Services and fees
PHASE 1 Books cleanup and catch-up
• Reconstruct and reconcile the books for the catch-up period from bank statements.
$2,700 + HST
PHASE 2 Monthly bookkeeping and HST filing
• Monthly bookkeeping: transaction coding, bank reconciliation, QuickBooks maintenance.
• Prepare and file the quarterly GST/HST return.
$1,250/month + HST
"""

APPRAISAL = """Northshore Appraisal Group
APPRAISAL REPORT
Effective date: 2027-05-15
Land value: $1,150,000
Appraised value as complete: $2,757,000
"""

COMMITMENT = """Meridian Construction Lending
LETTER OF COMMITMENT
Loan amount: $1,000,000
Interest rate: 8.5% per annum
Maturity date: 2028-05-31
"""

STATEMENT = """Meridian Credit Union
Statement of account
Account number: 1234-56789
2027-04-01 to 2027-04-30
Opening balance $20,000.00
Closing balance $34,794.96
"""


class TestLetterParsing:
    def test_template_letter_parses_phases_fees_and_authorisation(self):
        letter = parse_engagement_letter(LETTER)
        assert letter.client_name == "Jane Example" and letter.other_parties == ("John Example",)
        assert letter.subject.startswith("12 Sample Road") and letter.letter_date == date(2027, 3, 3)
        assert [p.number for p in letter.phases] == [1, 2, 3]
        assert [p.fee for p in letter.phases] == [Money.from_decimal("1500.00"), Money.from_decimal("600.00"), Money.from_decimal("2200.00")]
        assert [p.cadence for p in letter.phases] == ["fixed", "monthly", "fixed"]
        assert [p.authorised for p in letter.phases] == [True, False, False]
        assert letter.hourly_rate == Money.from_decimal("200.00")
        assert len(letter.phases[0].deliverables) == 3 and "self-supply" in letter.phases[0].deliverables[1]
        assert letter.assumptions and letter.exclusions and letter.warnings == ()

    def test_multi_phase_authorisation_and_monthly_fee_on_one_line(self):
        letter = parse_engagement_letter(BOOKKEEPING_LETTER)
        assert [p.authorised for p in letter.phases] == [True, True]
        assert letter.phases[1].cadence == "monthly" and letter.phases[1].fee == Money.from_decimal("1250.00")
        assert letter.phases[0].fee == Money.from_decimal("2700.00")

    def test_types_are_detected_without_stray_extras(self):
        assert detect_types(parse_engagement_letter(LETTER)) == [("development_rental", detect_types(parse_engagement_letter(LETTER))[0][1])]
        keys = [k for k, _ in detect_types(parse_engagement_letter(BOOKKEEPING_LETTER))]
        assert keys[0] in ("cleanup", "monthly_bookkeeping") and set(keys) <= {"cleanup", "monthly_bookkeeping", "sales_tax"}

    def test_a_letter_outside_the_template_says_so(self):
        letter = parse_engagement_letter("Dear client, we will do your books for $500 a month. Regards.")
        assert letter.phases == () and any("template" in w for w in letter.warnings)

    def test_record_from_letter_carries_deliverables_requests_and_fees(self):
        letter = parse_engagement_letter(LETTER)
        rec = EngagementRecord.from_letter(letter, client_id="C", engagement_id="ENG-1", today=TODAY)
        assert rec.primary_type == "development_rental" and rec.facts_schema == "realestate"
        assert {d.status for d in rec.deliverables if d.phase == 1} == {"not_started"}
        assert {d.status for d in rec.deliverables if d.phase > 1} == {"not_authorised"}
        kinds = [r.kind for r in rec.document_requests]
        assert kinds[0] == "engagement_letter" and len(kinds) == len(set(kinds))
        p = rec.progress()
        assert p["fixed_fees"] == "3700.00" and p["monthly_fees"] == "600.00"
        rec.authorise(2)
        assert {d.status for d in rec.deliverables if d.phase == 2} == {"not_started"}
        assert rec.mark(1, "Review the Client", "delivered", on=TODAY)
        assert rec.progress()["deliverables_delivered"] == 1

    def test_every_type_has_requests_questions_and_services(self):
        for t in ENGAGEMENT_TYPES.values():
            assert t.document_requests and t.questions and t.services and t.workflows


class TestIntake:
    def test_classification_by_words_and_filename(self):
        assert classify(APPRAISAL) == "appraisal"
        assert classify(COMMITMENT) == "loan_commitment"
        assert classify(STATEMENT) == "bank_statement"
        assert classify(LETTER) == "engagement_letter"
        assert classify("", "March-statement.pdf") == "bank_statement"

    def test_deterministic_facts(self):
        assert deterministic_facts(APPRAISAL, "appraisal") == {"appraised_value": "2757000", "appraisal_effective_date": "2027-05-15", "land_value": "1150000"}
        loan = deterministic_facts(COMMITMENT, "loan_commitment")
        assert loan["loan_limit"] == "1000000" and loan["interest_rate"] == "0.085" and loan["maturity_date"] == "2028-05-31"
        assert loan["lender"] == "Meridian Construction Lending"
        bank = deterministic_facts(STATEMENT, "bank_statement")
        assert bank["closing_balance"] == "34794.96" and bank["period_end"] == "2027-04-30" and bank["account_number_masked"] == "****6789"

    def test_proposals_target_the_engagement_schema(self):
        ups = propose_facts_updates("appraisal", {"appraised_value": "2757000", "land_value": "1150000"}, document_id="D1", schema="realestate")
        assert {u.path for u in ups} == {"appraised_completed_value", "land_fmv_at_change_of_use"}
        assert propose_facts_updates("appraisal", {"appraised_value": "1"}, document_id="D1", schema=None) == []
        loan = propose_facts_updates("loan_commitment", {"loan_limit": "5", "lender": "X"}, document_id="D2", schema="realestate")
        assert loan[0].apply == "append" and loan[0].path == "facilities"

    def test_labelled_answers_are_matched(self):
        qs = [("Q1", "Citizens?"), ("Q2", "Who registers?"), ("Q10", "Agent?")]
        got = dict(deterministic_answers("Hi\nQ1: yes both of us\nQ10: the property manager\n\nThanks", qs))
        assert got == {"Q1": "yes both of us", "Q10": "the property manager"}

    def test_set_path_handles_nested_and_lists(self):
        d: dict = {}
        _set_path(d, "a.b", 1)
        _set_path(d, "complexes[1].units", 4)
        _set_path(d, "facilities", {"x": 1}, append=True)
        _set_path(d, "facilities", {"y": 2}, append=True)
        assert d == {"a": {"b": 1}, "complexes": [{}, {"units": 4}], "facilities": [{"x": 1}, {"y": 2}]}


@pytest.fixture()
def client(tmp_path):
    profile = ClientProfile(client_id="JANE", business_name="Jane Example", province="ON", naics_code="531110")
    return ClientContext.create("JANE", tmp_path, profile)


class TestClientContext:
    def test_engagement_letter_sets_the_client_up(self, client):
        rec = client.add_document(LETTER.encode(), "letter.txt", today=TODAY)
        assert rec.kind == "engagement_letter" and client.engagement is not None
        e = client.engagement
        assert e.types == ["development_rental"] and e.letter.parsed_by == "template"
        assert next(r for r in e.document_requests if r.kind == "engagement_letter").status == "received"
        assert {"project_accounting", "rental_property", "hst"} <= set(client.profile.services)
        assert client.profile.owner_name == "Jane Example"
        assert len(list(client.knowledge.unanswered())) == len(ENGAGEMENT_TYPES["development_rental"].questions)
        facts = client.load_facts("realestate")
        assert facts["client_name"] == "Jane Example" and facts["phases"][0]["fee"] == "1500.00" and facts["appraised_completed_value"] is None
        assert [x["kind"] for x in client.timeline()] == ["client_created", "document_received", "facts_seeded", "engagement_created"]

    def test_documents_add_facts_match_requests_and_propose_updates(self, client):
        client.add_document(LETTER.encode(), "letter.txt", today=TODAY)
        a = client.add_document(APPRAISAL.encode(), "appraisal.txt", today=TODAY, source="email")
        c = client.add_document(COMMITMENT.encode(), "commitment.txt", today=TODAY, source="email")
        assert a.kind == "appraisal" and a.matched_request and c.matched_request
        assert client.knowledge.facts["appraisal:appraised_value"].value == "2757000"
        assert client.knowledge.facts["appraisal:appraised_value"].source == f"document:{a.document_id}"
        pending = client.pending_updates()
        assert {u.path for _, u in pending} == {"appraised_completed_value", "land_fmv_at_change_of_use", "facilities"}
        applied = client.apply_updates()
        assert len(applied) == 3 and not client.pending_updates()
        facts = client.load_facts("realestate")
        assert facts["appraised_completed_value"] == "2757000" and facts["facilities"][0]["limit"] == "1000000"
        assert (client.directory / "documents" / a.stored_as).exists()

    def test_duplicates_are_recognised_not_stored_twice(self, client):
        first = client.add_document(APPRAISAL.encode(), "appraisal.txt", today=TODAY)
        again = client.add_document(APPRAISAL.encode(), "appraisal-copy.txt", today=TODAY)
        assert again.duplicate_of == first.document_id
        assert len([d for d in client.documents if not d.duplicate_of]) == 1

    def test_replies_answer_questions_and_propose_citizenship(self, client):
        client.add_document(LETTER.encode(), "letter.txt", today=TODAY)
        r = client.add_response("Hi,\nQ1: Yes, we are both Canadian citizens.\n\nJane", today=TODAY, sender="client", subject="Re: questions")
        assert r.kind == "client_response" and r.answered_questions == ["Q1"]
        assert client.knowledge.questions["Q1"].answer.startswith("Yes")
        assert any(u.path == "client_citizenship" and u.value == "canadian" for _, u in client.pending_updates())
        qid = client.ask("What is the expected rent per unit?")
        client.answer(qid, "2900 a month", by="phone")
        assert client.knowledge.questions[qid].answer == "2900 a month"

    def test_reload_and_status_are_complete(self, client, tmp_path):
        client.add_document(LETTER.encode(), "letter.txt", today=TODAY)
        client.add_document(STATEMENT.encode(), "april.txt", today=TODAY)
        again = ClientContext.load("JANE", tmp_path)
        s = again.status()
        assert s["documents"] == 2 and s["documents_by_kind"] == {"engagement_letter": 1, "bank_statement": 1}
        assert s["engagement"]["authorised_phases"] == [1] and s["open_questions"]
        assert any(line.startswith("engagement: development_rental") for line in again.context_lines())
        assert ClientContext.list_ids(tmp_path) == ["JANE"]
        with pytest.raises(FileExistsError):
            ClientContext.create("JANE", tmp_path, client.profile)

    def test_facts_file_feeds_the_engagement_plan_after_filling(self, client):
        client.add_document(LETTER.encode(), "letter.txt", today=TODAY)
        client.add_document(APPRAISAL.encode(), "appraisal.txt", today=TODAY)
        client.add_document(COMMITMENT.encode(), "commitment.txt", today=TODAY)
        client.apply_updates()
        raw = client.load_facts("realestate")
        raw.update({"construction_start": "2026-06-01", "expected_completion": "2027-05-01", "budget_low": "1500000.00", "budget_high": "1600000.00",
                    "spent_to_date": "250000.00", "purchase_date": "2018-06-01", "purchase_price": "690000.00", "change_of_use_date": "2026-06-01",
                    "complexes": [{"name": "Fourplex", "units": 4, "expected_fmv": "2300000.00"}]})
        facts = EngagementFacts.from_dict({k: v for k, v in raw.items() if not k.startswith("_")})
        assert facts.appraised_completed_value == Money.from_decimal("2757000") and facts.facilities[0].limit == Money.from_decimal("1000000")

    def test_run_client_reads_the_client_folder(self, client, clean_company):
        from forge.learning import OutcomeLog

        client.knowledge.approve("rule:FOS-R027", "Known slow payer, chase monthly", by="amro")
        client.save()
        cr = run_client(client.profile, clean_company.ledger, period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
                        statements=clean_company.statements, data_dir=client.directory)
        assert cr.run.passed_data_gate
        assert OutcomeLog.load("JANE", client.directory).path == client.directory / "JANE.outcomes.json"
        json.loads((client.directory / "documents" / "index.json").read_text())
