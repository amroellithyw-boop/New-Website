"""The QuickBooks connector, tested against recorded payload shapes.

There is no live company file in CI, so these tests use the response shapes
QuickBooks actually returns. That is enough to prove the mapping, the tie-out
and the read-only boundary; it is not enough to prove the integration, which is
what a sandbox company is for.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from forge.canonical.enums import AccountSubtype, AccountType
from forge.connectors.qbo import (
    QboConnector,
    compare_trial_balance,
    parse_trial_balance_report,
)
from forge.connectors.qbo_auth import (
    MemoryTokenStore,
    QboAuth,
    QboAuthError,
    QboConfig,
    QboReadClient,
    QboTokens,
    escape_query_value,
)
from forge.money import Money
from forge.normalize.qbo_mapper import MappingError, map_account, map_journal_entry

CONFIG = QboConfig("client-id", "client-secret", "https://app.example/cb", sandbox=True)


def _tokens(expires_in_seconds: int = 3600) -> QboTokens:
    now = datetime.now(UTC)
    return QboTokens(
        realm_id="4620816365",
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=now + timedelta(seconds=expires_in_seconds),
        refresh_expires_at=now + timedelta(days=100),
    )


class FakeHttp:
    """Records calls and replays canned responses."""

    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


class TestAuth:
    def test_authorize_url_carries_state_and_scope(self):
        url = QboAuth(CONFIG).authorize_url("nonce-abc")
        assert "state=nonce-abc" in url
        assert "com.intuit.quickbooks.accounting" in url

    def test_exchange_stores_tokens(self):
        http = FakeHttp([(200, {
            "access_token": "a1", "refresh_token": "r1",
            "expires_in": 3600, "x_refresh_token_expires_in": 8726400,
        })])
        store = MemoryTokenStore()
        auth = QboAuth(CONFIG, store, http=http)
        tokens = auth.exchange_code("TEN-1", "code", "4620816365")
        assert tokens.realm_id == "4620816365"
        assert store.load("TEN-1") is not None

    def test_refresh_persists_the_rotated_token_before_returning(self):
        """Intuit rotates the refresh token; losing it forces a reconnect."""
        store = MemoryTokenStore()
        store.save("TEN-1", _tokens(expires_in_seconds=-10))
        http = FakeHttp([(200, {
            "access_token": "a2", "refresh_token": "r2-rotated", "expires_in": 3600,
        })])
        auth = QboAuth(CONFIG, store, http=http)
        tokens = auth.access_token("TEN-1")
        assert tokens.access_token == "a2"
        assert store.load("TEN-1").refresh_token == "r2-rotated"

    def test_expired_refresh_token_fails_with_a_useful_message(self):
        store = MemoryTokenStore()
        stale = _tokens(expires_in_seconds=-10)
        stale.refresh_expires_at = datetime.now(UTC) - timedelta(days=1)
        store.save("TEN-1", stale)
        auth = QboAuth(CONFIG, store, http=FakeHttp([]))
        with pytest.raises(QboAuthError, match="re-authorise"):
            auth.access_token("TEN-1")

    def test_unconnected_tenant_fails_clearly(self):
        auth = QboAuth(CONFIG, MemoryTokenStore(), http=FakeHttp([]))
        with pytest.raises(QboAuthError, match="has not connected"):
            auth.access_token("TEN-NOBODY")


class TestQueryEscaping:
    def test_single_quotes_are_doubled(self):
        assert escape_query_value("O'Brien") == "O''Brien"

    def test_backslash_is_rejected(self):
        with pytest.raises(ValueError):
            escape_query_value("a\\b")

    def test_control_characters_are_rejected(self):
        with pytest.raises(ValueError):
            escape_query_value("drop\x00table")


class TestReadOnlyBoundary:
    def test_the_read_client_has_no_write_method(self):
        """Read-only is enforced by the type, because the OAuth scope grants write."""
        forbidden = {"post", "create", "update", "delete", "write", "send"}
        assert not forbidden & set(dir(QboReadClient))

    def test_the_connector_has_no_write_method(self):
        forbidden = {"post", "create", "update", "delete", "write", "send"}
        assert not forbidden & set(dir(QboConnector))


class TestAccountMapping:
    def test_classification_drives_the_statement_section(self):
        account = map_account(
            {"Id": "33", "Name": "Chequing", "AcctNum": "1000",
             "Classification": "Asset", "AccountSubType": "Checking", "Active": True},
            "ENT-1",
        )
        assert account.type is AccountType.ASSET
        assert account.subtype is AccountSubtype.BANK
        assert account.is_control_account

    def test_unknown_subtype_degrades_to_the_right_class(self):
        account = map_account(
            {"Id": "44", "Name": "Odd", "Classification": "Expense",
             "AccountSubType": "SomethingIntuitAddedLastWeek"},
            "ENT-1",
        )
        assert account.type is AccountType.EXPENSE
        assert account.subtype is AccountSubtype.OTHER_EXPENSE

    def test_unknown_classification_refuses_to_guess(self):
        """Guessing puts the balance on the wrong side of the balance sheet."""
        with pytest.raises(MappingError, match="classification"):
            map_account({"Id": "9", "Name": "?", "Classification": "Mystery"}, "ENT-1")

    def test_accumulated_depreciation_maps_to_the_contra_subtype(self):
        account = map_account(
            {"Id": "55", "Name": "Accum Dep", "Classification": "Asset",
             "AccountSubType": "AccumulatedDepreciation"},
            "ENT-1",
        )
        assert account.subtype is AccountSubtype.ACCUMULATED_DEPRECIATION
        assert account.is_contra

    def test_lineage_is_attached(self):
        account = map_account(
            {"Id": "33", "Name": "Chequing", "Classification": "Asset",
             "AccountSubType": "Checking",
             "MetaData": {"LastUpdatedTime": "2026-06-30T10:00:00-04:00"}},
            "ENT-1",
        )
        assert account.lineage is not None
        assert account.lineage.source_id == "Account:33"
        assert account.lineage.mapping_version == "qbo-1"
        assert account.lineage.synced_at is not None


def _je(txn_id: str, lines: list[tuple[str, str, float]], txn_date: str = "2026-06-30") -> dict:
    return {
        "Id": txn_id,
        "TxnDate": txn_date,
        "DocNumber": f"JE-{txn_id}",
        "PrivateNote": "Test entry",
        "SyncToken": "0",
        "Line": [
            {
                "Id": str(i),
                "Amount": amount,
                "DetailType": "JournalEntryLineDetail",
                "JournalEntryLineDetail": {
                    "PostingType": posting,
                    "AccountRef": {"value": account, "name": f"Account {account}"},
                },
            }
            for i, (account, posting, amount) in enumerate(lines)
        ],
        "MetaData": {
            "CreateTime": "2026-06-30T10:00:00-04:00",
            "LastUpdatedTime": "2026-06-30T10:00:00-04:00",
        },
    }


class TestJournalEntryMapping:
    def test_posting_type_becomes_a_signed_amount(self):
        txn = map_journal_entry(_je("1", [("70", "Debit", 7450.00), ("71", "Credit", 7450.00)]), "ENT-1")
        assert txn.lines[0].amount == Money.from_decimal("7450.00")
        assert txn.lines[1].amount == Money.from_decimal("-7450.00")
        assert txn.is_balanced

    def test_amounts_never_pass_through_a_float_rounding(self):
        txn = map_journal_entry(
            _je("2", [("70", "Debit", 1234.56), ("71", "Credit", 1234.56)]), "ENT-1"
        )
        assert txn.lines[0].amount.minor_units == 123456

    def test_an_unbalanced_payload_is_refused(self):
        with pytest.raises(MappingError, match="does not balance"):
            map_journal_entry(_je("3", [("70", "Debit", 100.00), ("71", "Credit", 90.00)]), "ENT-1")

    def test_a_line_without_an_account_is_refused(self):
        payload = _je("4", [("70", "Debit", 10.00), ("71", "Credit", 10.00)])
        payload["Line"][0]["JournalEntryLineDetail"]["AccountRef"] = {}
        with pytest.raises(MappingError, match="no account reference"):
            map_journal_entry(payload, "ENT-1")

    def test_an_unknown_posting_type_is_refused(self):
        payload = _je("5", [("70", "Debit", 10.00), ("71", "Credit", 10.00)])
        payload["Line"][0]["JournalEntryLineDetail"]["PostingType"] = "Sideways"
        with pytest.raises(MappingError, match="posting type"):
            map_journal_entry(payload, "ENT-1")

    def test_modification_time_is_preserved_for_post_close_detection(self):
        txn = map_journal_entry(_je("6", [("70", "Debit", 10.00), ("71", "Credit", 10.00)]), "ENT-1")
        assert txn.last_modified_at is not None
        assert txn.created_at is not None


TRIAL_BALANCE_REPORT = {
    "Header": {"ReportName": "TrialBalance", "Currency": "CAD",
               "StartPeriod": "2026-01-01", "EndPeriod": "2026-06-30"},
    "Columns": {"Column": [{"ColTitle": ""}, {"ColTitle": "Debit"}, {"ColTitle": "Credit"}]},
    "Rows": {
        "Row": [
            {"type": "Data", "ColData": [
                {"value": "Chequing", "id": "33"}, {"value": "84250.00"}, {"value": ""}]},
            {"type": "Data", "ColData": [
                {"value": "Accounts Receivable", "id": "34"}, {"value": "96400.00"}, {"value": ""}]},
            {"type": "Section", "Rows": {"Row": [
                {"type": "Data", "ColData": [
                    {"value": "Accounts Payable", "id": "40"}, {"value": ""}, {"value": "58300.00"}]},
            ]}, "Summary": {"ColData": [
                {"value": "Total Liabilities"}, {"value": ""}, {"value": "58300.00"}]}},
            {"type": "Data", "ColData": [
                {"value": "Retained Earnings", "id": "50"}, {"value": ""}, {"value": "122350.00"}]},
        ]
    },
}


class TestTrialBalanceReport:
    def test_leaf_rows_are_parsed(self):
        tb = parse_trial_balance_report(TRIAL_BALANCE_REPORT, date(2026, 6, 30))
        assert len(tb.rows) == 4
        assert tb.currency == "CAD"

    def test_section_summaries_are_not_double_counted(self):
        tb = parse_trial_balance_report(TRIAL_BALANCE_REPORT, date(2026, 6, 30))
        assert tb.total_debits == Money.from_decimal("180650.00")
        assert tb.total_credits == Money.from_decimal("180650.00")
        assert tb.is_balanced

    def test_net_is_debit_positive(self):
        tb = parse_trial_balance_report(TRIAL_BALANCE_REPORT, date(2026, 6, 30))
        by_id = tb.by_account_id()
        assert by_id["33"] == Money.from_decimal("84250.00")
        assert by_id["40"] == Money.from_decimal("-58300.00")


class TestTieOut:
    def _ledger_from(self, connector_records):
        return QboConnector.build_ledger(
            connector_records, tenant_id="TEN-1", entity_id="ENT-1", entity_name="Test Co",
        )

    def test_a_faithful_rebuild_ties_exactly(self):
        from forge.connectors.base import RawRecord

        now = datetime.now(UTC)
        accounts = [
            {"Id": "33", "Name": "Chequing", "Classification": "Asset", "AccountSubType": "Checking"},
            {"Id": "50", "Name": "Retained Earnings", "Classification": "Equity",
             "AccountSubType": "RetainedEarnings"},
        ]
        records = [
            RawRecord(SOURCE := "quickbooks_online", "Account", a["Id"], a, now) for a in accounts
        ]
        records.append(
            RawRecord(
                SOURCE, "JournalEntry", "1",
                _je("1", [("33", "Debit", 84250.00), ("50", "Credit", 84250.00)]), now,
            )
        )
        ledger, failures = self._ledger_from(records)
        assert failures == []

        source = parse_trial_balance_report(
            {
                "Header": {"Currency": "CAD"},
                "Rows": {"Row": [
                    {"type": "Data", "ColData": [
                        {"value": "Chequing", "id": "33"}, {"value": "84250.00"}, {"value": ""}]},
                    {"type": "Data", "ColData": [
                        {"value": "Retained Earnings", "id": "50"}, {"value": ""},
                        {"value": "84250.00"}]},
                ]},
            },
            date(2026, 6, 30),
        )
        result = compare_trial_balance(source, ledger, date(2026, 6, 30))
        assert result.ties, result.report()

    def test_a_missing_transaction_fails_the_gate(self):
        from forge.connectors.base import RawRecord

        now = datetime.now(UTC)
        records = [
            RawRecord("quickbooks_online", "Account", "33",
                      {"Id": "33", "Name": "Chequing", "Classification": "Asset",
                       "AccountSubType": "Checking"}, now),
            RawRecord("quickbooks_online", "Account", "50",
                      {"Id": "50", "Name": "Retained Earnings", "Classification": "Equity",
                       "AccountSubType": "RetainedEarnings"}, now),
            RawRecord("quickbooks_online", "JournalEntry", "1",
                      _je("1", [("33", "Debit", 50000.00), ("50", "Credit", 50000.00)]), now),
        ]
        ledger, _failures = self._ledger_from(records)
        source = parse_trial_balance_report(
            {
                "Header": {"Currency": "CAD"},
                "Rows": {"Row": [
                    {"type": "Data", "ColData": [
                        {"value": "Chequing", "id": "33"}, {"value": "84250.00"}, {"value": ""}]},
                    {"type": "Data", "ColData": [
                        {"value": "Retained Earnings", "id": "50"}, {"value": ""},
                        {"value": "84250.00"}]},
                ]},
            },
            date(2026, 6, 30),
        )
        result = compare_trial_balance(source, ledger, date(2026, 6, 30))
        assert not result.ties
        assert result.largest_difference == Money.from_decimal("34250.00")
        assert "out by" in result.report()

    def test_unmapped_entities_are_reported_not_swallowed(self):
        from forge.connectors.base import RawRecord

        now = datetime.now(UTC)
        records = [
            RawRecord("quickbooks_online", "Invoice", "7", {"Id": "7"}, now),
        ]
        _ledger, failures = self._ledger_from(records)
        assert any("Invoice" in f for f in failures)
