"""The handover surfaces: firm config, .env loading, recorded QuickBooks exports
and their anonymisation. Everything the owner fills in, proven to be read."""

from __future__ import annotations

import json
import os
from datetime import date

import pytest

from forge.clients import ClientProfile
from forge.connectors.qbo import QboConnector, compare_trial_balance
from forge.connectors.qbo_auth import FileTokenStore, QboAuthError, QboConfig, QboTokens
from forge.connectors.qbo_export import (
    anonymise,
    load_export,
    records_from_export,
    trial_balance_from_export,
)
from forge.firm import FirmConfig
from forge.growth import build_proposal, diagnostic_fee, recommend_engagement, render_proposal
from forge.money import Money
from tests.test_qbo import _je


def _export() -> dict:
    return {
        "format": "forgeos-qbo-export/1",
        "pulled_at": "2026-09-15T12:00:00+00:00",
        "period_start": "2026-01-01",
        "period_end": "2026-06-30",
        "records": {
            "CompanyInfo": [{"Id": "1", "CompanyName": "Acme Paving Inc", "LegalName": "Acme Paving Inc",
                             "CompanyAddr": {"Line1": "1 Main St"}, "PrimaryPhone": {"FreeFormNumber": "416-555-0100"}}],
            "Account": [
                {"Id": "33", "Name": "Chequing", "Classification": "Asset", "AccountSubType": "Checking", "AcctNum": "1000"},
                {"Id": "50", "Name": "Retained Earnings", "Classification": "Equity", "AccountSubType": "RetainedEarnings"},
            ],
            "Customer": [{"Id": "7", "DisplayName": "Stonegate Industrial Park", "CompanyName": "Stonegate Industrial Park",
                          "PrimaryEmailAddr": {"Address": "ap@stonegate.ca"}, "BillAddr": {"Line1": "9 Quarry Rd"},
                          "Notes": "Slow payer", "Taxable": True}],
            "Vendor": [{"Id": "12", "DisplayName": "Halloway Excavating Ltd", "GivenName": "Jim", "FamilyName": "Halloway",
                        "PrimaryPhone": {"FreeFormNumber": "905-555-0199"}, "TaxIdentifier": "123456789RT0001"}],
            "JournalEntry": [_je("1", [("33", "Debit", 84250.00), ("50", "Credit", 84250.00)]) | {
                "PrivateNote": "Deposit from Stonegate Industrial Park re Halloway Excavating Ltd job"}],
        },
        "trial_balance": {
            "Header": {"Currency": "CAD"},
            "Rows": {"Row": [
                {"type": "Data", "ColData": [{"value": "Chequing", "id": "33"}, {"value": "84250.00"}, {"value": ""}]},
                {"type": "Data", "ColData": [{"value": "Retained Earnings", "id": "50"}, {"value": ""}, {"value": "84250.00"}]},
            ]},
        },
    }


class TestRecordedExport:
    def test_export_replays_to_a_tie_out(self, tmp_path):
        path = tmp_path / "acme.json"
        path.write_text(json.dumps(_export()))
        export = load_export(path)
        records = records_from_export(export)
        ledger, failures = QboConnector.build_ledger(records, tenant_id="T", entity_id="E", entity_name="Acme")
        assert failures == []
        result = compare_trial_balance(trial_balance_from_export(export), ledger, date(2026, 6, 30), start=date(2026, 1, 1))
        assert result.ties, result.report()

    def test_wrong_format_is_refused(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text(json.dumps({"format": "something-else"}))
        with pytest.raises(ValueError):
            load_export(path)


class TestAnonymisation:
    def test_names_are_replaced_consistently_everywhere(self):
        out = anonymise(_export())
        text = json.dumps(out)
        for original in ("Stonegate Industrial Park", "Halloway Excavating Ltd", "Acme Paving Inc", "Jim", "Halloway"):
            assert original not in text
        cust = out["records"]["Customer"][0]
        vend = out["records"]["Vendor"][0]
        assert cust["DisplayName"] == "Customer 0001" and vend["DisplayName"] == "Vendor 0001"
        note = out["records"]["JournalEntry"][0]["PrivateNote"]
        assert "Customer 0001" in note and "Vendor 0001" in note
        assert out["anonymised"] and out["anonymised_parties"] >= 4

    def test_contact_details_and_identifiers_are_stripped(self):
        out = anonymise(_export())
        cust = out["records"]["Customer"][0]
        vend = out["records"]["Vendor"][0]
        for k in ("PrimaryEmailAddr", "BillAddr", "Notes"):
            assert k not in cust
        for k in ("PrimaryPhone", "TaxIdentifier"):
            assert k not in vend
        assert "AcctNum" not in out["records"]["Account"][0]
        assert "PrimaryPhone" not in out["records"]["CompanyInfo"][0]

    def test_amounts_dates_and_accounts_are_untouched(self):
        before, after = _export(), anonymise(_export())
        assert after["records"]["JournalEntry"][0]["Line"] == before["records"]["JournalEntry"][0]["Line"]
        assert after["trial_balance"] == before["trial_balance"]
        assert after["records"]["Account"][0]["Name"] == "Chequing"

    def test_anonymised_export_still_ties(self):
        export = anonymise(_export())
        ledger, failures = QboConnector.build_ledger(records_from_export(export), tenant_id="T", entity_id="E", entity_name="X")
        assert failures == []
        assert compare_trial_balance(trial_balance_from_export(export), ledger, date(2026, 6, 30), start=date(2026, 1, 1)).ties


class TestQboConfiguration:
    def test_config_from_environment_names_what_is_missing(self, monkeypatch):
        for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REDIRECT_URI", "QBO_SANDBOX"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(QboAuthError) as exc:
            QboConfig.from_environment()
        assert "QBO_CLIENT_ID" in str(exc.value) and "QBO_CLIENT_SECRET" in str(exc.value)

    def test_config_from_environment_defaults_to_sandbox(self, monkeypatch):
        monkeypatch.setenv("QBO_CLIENT_ID", "id")
        monkeypatch.setenv("QBO_CLIENT_SECRET", "secret")
        monkeypatch.delenv("QBO_SANDBOX", raising=False)
        cfg = QboConfig.from_environment()
        assert cfg.sandbox and cfg.redirect_uri.startswith("http://localhost:8765")
        monkeypatch.setenv("QBO_SANDBOX", "false")
        assert not QboConfig.from_environment().sandbox

    def test_file_token_store_round_trips_and_is_private(self, tmp_path):
        from datetime import UTC, datetime, timedelta

        store = FileTokenStore(tmp_path / "qbo")
        assert store.load("acme") is None
        tokens = QboTokens("123", "access", "refresh", datetime.now(UTC) + timedelta(hours=1))
        store.save("acme", tokens)
        again = store.load("acme")
        assert again is not None and again.refresh_token == "refresh" and again.realm_id == "123"
        if os.name == "posix":
            assert (tmp_path / "qbo" / "acme.qbo-tokens.json").stat().st_mode & 0o077 == 0


class TestFirmConfig:
    def _profile(self) -> ClientProfile:
        return ClientProfile(client_id="C", business_name="X", naics_code="238210",
                             annual_revenue_estimate=Money.from_decimal("2400000.00"),
                             services=("bookkeeping", "payroll", "cfo_advisory"), employees_full_time=4)

    def test_defaults_apply_without_a_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORGE_FIRM", str(tmp_path / "missing.json"))
        fc = FirmConfig.load()
        assert fc.name == "Profit Forge" and fc.source == ""
        assert recommend_engagement(self._profile(), fc) == recommend_engagement(self._profile())

    def test_file_overrides_identity_and_pricing(self, tmp_path):
        path = tmp_path / "firm.json"
        path.write_text(json.dumps({"name": "Forge Co", "sender": "A. Person", "base_by_tier": {"small": "1500.00"},
                                    "service_fees": {"cfo_advisory": "1200.00"}, "payroll_per_employee": "20.00",
                                    "diagnostic_by_tier": {"small": "3000.00"}, "unknown_key": 1}))
        fc = FirmConfig.load(path)
        assert fc.name == "Forge Co" and fc.source == str(path)
        e = recommend_engagement(self._profile(), fc)
        by_label = dict(e.breakdown)
        assert by_label["Base, small"] == Money.from_decimal("1500.00")
        assert by_label["Virtual CFO Advisory"] == Money.from_decimal("1200.00")
        assert by_label["Payroll Processing"] == Money.from_decimal("45.00") + Money.from_decimal("20.00").scale(4)
        assert diagnostic_fee(self._profile(), fc) == Money.from_decimal("3000.00")
        assert e.diagnostic_fee == Money.from_decimal("3000.00")

    def test_example_file_matches_the_code_defaults(self):
        from pathlib import Path

        example = FirmConfig.load(Path(__file__).resolve().parents[1] / "firm.example.json")
        assert example.source
        assert recommend_engagement(self._profile(), example).monthly_fee == recommend_engagement(self._profile()).monthly_fee
        assert diagnostic_fee(self._profile(), example) == diagnostic_fee(self._profile())

    def test_proposal_carries_the_firm_price(self, clean_run):
        fc = FirmConfig(name="Forge Co", sender="A. Person", base_by_tier={"small": "1500.00"})
        p = build_proposal(self._profile(), clean_run, today=date(2026, 6, 30), firm=fc)
        text = render_proposal(p, firm=fc.name, sender=fc.sender)
        assert "Forge Co" in text and text.rstrip().endswith("A. Person")
        assert "$1,500.00" in text


class TestDotenv:
    def test_env_file_fills_gaps_without_overriding_the_shell(self, tmp_path, monkeypatch):
        from forge.cli import _load_dotenv

        monkeypatch.setenv("ALREADY_SET", "shell")
        monkeypatch.delenv("FROM_FILE", raising=False)
        env = tmp_path / ".env"
        env.write_text("# comment\nALREADY_SET=file\nFROM_FILE=\"value\"\nEMPTY=\n")
        _load_dotenv(env)
        assert os.environ["ALREADY_SET"] == "shell"
        assert os.environ["FROM_FILE"] == "value"
        assert "EMPTY" not in os.environ
