"""The read-only QuickBooks Online connector.

This closes the last gap between the engine and a paying client: the ability to
point ForgeOS at a real company file and have it reproduce the books exactly.

The design point that matters is the tie-out. Rather than trusting that the sync
pulled everything, the connector fetches QuickBooks' *own* trial balance report
and compares it, account by account, against the trial balance the engine
rebuilds from the synced transactions. If the two disagree by a cent, the data
gate fails and nothing downstream is presented as fact. That single check is
what turns "we imported your books" into "we reproduced your books".
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from ..canonical.enums import PartyType
from ..canonical.models import Account, Ledger, LegalEntity, Party, Tenant
from ..money import Money, msum
from ..normalize.qbo_mapper import (
    MappingError,
    map_account,
    map_journal_entry,
    map_party,
)
from .base import Connector, RawRecord, SyncCursor
from .qbo_auth import QboReadClient

__all__ = [
    "QboConnector",
    "TrialBalanceRow",
    "QboTrialBalance",
    "TieOutResult",
    "parse_trial_balance_report",
    "compare_trial_balance",
]

# Entities pulled on a full sync, in dependency order.
SYNC_ENTITIES: tuple[str, ...] = (
    "CompanyInfo",
    "Account",
    "Customer",
    "Vendor",
    "Class",
    "JournalEntry",
    "Invoice",
    "CreditMemo",
    "Payment",
    "SalesReceipt",
    "Bill",
    "VendorCredit",
    "BillPayment",
    "Purchase",
    "Deposit",
    "Transfer",
)


class QboConnector(Connector):
    """Read-only adapter. Produces raw payloads and canonical records, nothing else."""

    source_system = "quickbooks_online"

    def __init__(self, client: QboReadClient, *, entities: Sequence[str] = SYNC_ENTITIES) -> None:
        self._client = client
        self._entities = tuple(entities)

    def resources(self) -> tuple[str, ...]:
        return self._entities

    def fetch(self, resource: str, *, since: SyncCursor | None = None) -> Iterator[RawRecord]:
        """Yield raw payloads for one entity, incrementally where possible.

        QuickBooks exposes ``MetaData.LastUpdatedTime`` on every entity, which is
        what makes an incremental resync safe: the cursor holds the newest
        timestamp seen, and the next sync asks only for rows changed since. Every
        row still carries its own identifier, so a row returned twice replaces
        rather than duplicates.
        """
        where = None
        if since is not None and since.last_updated_at is not None:
            stamp = since.last_updated_at.astimezone(UTC).replace(tzinfo=None).isoformat()
            where = f"MetaData.LastUpdatedTime > '{stamp}'"

        fetched_at = datetime.now(UTC)
        for payload in self._client.query_all(resource, where=where):
            meta = payload.get("MetaData", {}) or {}
            updated = meta.get("LastUpdatedTime")
            yield RawRecord(
                source_system=self.source_system,
                resource=resource,
                source_id=str(payload.get("Id", "")),
                payload=payload,
                fetched_at=fetched_at,
                source_updated_at=(
                    datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                    if updated
                    else None
                ),
            )

    # ---- canonical assembly ---------------------------------------------

    @staticmethod
    def build_ledger(
        records: Sequence[RawRecord],
        *,
        tenant_id: str,
        entity_id: str,
        entity_name: str,
        currency: str = "CAD",
        jurisdiction: str = "CA-ON",
    ) -> tuple[Ledger, list[str]]:
        """Assemble a canonical ledger, returning it with any mapping failures.

        Failures are returned rather than raised so the caller can decide. They
        are never swallowed: an unreported mapping failure becomes a silent hole
        in the books, and the tie-out below is what proves there is no hole.
        """
        from ..canonical.models import FiscalCalendar

        failures: list[str] = []
        by_resource: dict[str, list[Mapping[str, Any]]] = {}
        for record in records:
            by_resource.setdefault(record.resource, []).append(record.payload)

        accounts: dict[str, Account] = {}
        for payload in by_resource.get("Account", []):
            try:
                account = map_account(payload, entity_id, currency)
                accounts[account.account_id] = account
            except MappingError as exc:
                failures.append(str(exc))

        parties: dict[str, Party] = {}
        for resource, party_type in (
            ("Customer", PartyType.CUSTOMER),
            ("Vendor", PartyType.VENDOR),
        ):
            for payload in by_resource.get(resource, []):
                try:
                    party = map_party(payload, entity_id, party_type)
                    parties[party.party_id] = party
                except (MappingError, KeyError) as exc:
                    failures.append(f"{resource}: {exc}")

        transactions = []
        for payload in by_resource.get("JournalEntry", []):
            try:
                transactions.append(map_journal_entry(payload, entity_id, currency))
            except MappingError as exc:
                failures.append(str(exc))

        for resource in (
            "Invoice", "CreditMemo", "Payment", "SalesReceipt", "Bill",
            "VendorCredit", "BillPayment", "Purchase", "Deposit", "Transfer",
        ):
            count = len(by_resource.get(resource, []))
            if count:
                failures.append(
                    f"{resource} normalisation is not implemented; {count} document(s) "
                    "were fetched and stored but not mapped into the ledger"
                )

        tenant = Tenant(tenant_id=tenant_id, name=entity_name, home_currency=currency)
        entity = LegalEntity(
            entity_id=entity_id, tenant_id=tenant_id, name=entity_name,
            currency=currency, jurisdiction=jurisdiction,
        )
        ledger = Ledger(
            tenant=tenant,
            entity=entity,
            calendar=FiscalCalendar(entity_id=entity_id),
            accounts=accounts,
            parties=parties,
            transactions=transactions,
        ).build_indexes()
        return ledger, failures

    # ---- the data gate ---------------------------------------------------

    def fetch_trial_balance(self, as_of: date, *, start: date | None = None) -> QboTrialBalance:
        """Fetch QuickBooks' own trial balance, to compare our rebuild against."""
        params = {
            "start_date": (start or date(as_of.year, 1, 1)).isoformat(),
            "end_date": as_of.isoformat(),
            "accounting_method": "Accrual",
        }
        payload = self._client.report("TrialBalance", **params)
        return parse_trial_balance_report(payload, as_of)


@dataclass(frozen=True)
class TrialBalanceRow:
    account_name: str
    account_id: str | None
    debit: Money
    credit: Money

    @property
    def net(self) -> Money:
        """Debit-positive net, matching the engine's convention."""
        return self.debit - self.credit


@dataclass
class QboTrialBalance:
    """QuickBooks' own trial balance, as reported by the source system."""

    as_of: date
    currency: str
    rows: tuple[TrialBalanceRow, ...] = ()

    @property
    def total_debits(self) -> Money:
        return msum((r.debit for r in self.rows), self.currency)

    @property
    def total_credits(self) -> Money:
        return msum((r.credit for r in self.rows), self.currency)

    @property
    def is_balanced(self) -> bool:
        return self.total_debits == self.total_credits

    def by_account_id(self) -> dict[str, Money]:
        out: dict[str, Money] = {}
        for row in self.rows:
            if row.account_id:
                out[row.account_id] = out.get(row.account_id, Money.zero(self.currency)) + row.net
        return out


def _cell_money(cell: Mapping[str, Any] | None, currency: str) -> Money:
    if not cell:
        return Money.zero(currency)
    value = cell.get("value")
    if value in (None, ""):
        return Money.zero(currency)
    return Money.from_decimal(Decimal(str(value)), currency)


def parse_trial_balance_report(payload: Mapping[str, Any], as_of: date) -> QboTrialBalance:
    """Parse the nested QuickBooks report structure into flat rows.

    QuickBooks reports are a recursive tree of ``Row`` objects mixing data rows,
    section headers and summaries. Only the leaf data rows carry balances, and
    summary rows must be skipped or every subtotal is counted twice.
    """
    header = payload.get("Header", {}) or {}
    currency = header.get("Currency", "CAD")
    rows: list[TrialBalanceRow] = []

    def walk(node: Mapping[str, Any]) -> None:
        for row in node.get("Row", []) or []:
            row_type = row.get("type")
            if row_type == "Section":
                if "Rows" in row:
                    walk(row["Rows"])
                # Summary rows inside a section are subtotals; skipping them is
                # what stops the totals doubling.
                continue
            col_data = (row.get("ColData") or [])
            if len(col_data) < 3:
                continue
            label = col_data[0].get("value", "")
            if not label:
                continue
            rows.append(
                TrialBalanceRow(
                    account_name=label,
                    account_id=col_data[0].get("id"),
                    debit=_cell_money(col_data[1], currency),
                    credit=_cell_money(col_data[2], currency),
                )
            )

    walk(payload.get("Rows", {}) or {})
    return QboTrialBalance(as_of=as_of, currency=currency, rows=tuple(rows))


@dataclass
class TieOutResult:
    """Whether the rebuilt ledger reproduces the source exactly."""

    as_of: date
    currency: str
    source_total: Money
    rebuilt_total: Money
    differences: tuple[tuple[str, Money, Money], ...] = ()
    """(account id, source net, rebuilt net) for every account that disagrees."""
    missing_accounts: tuple[str, ...] = ()
    extra_accounts: tuple[str, ...] = ()

    @property
    def ties(self) -> bool:
        return (
            not self.differences
            and not self.missing_accounts
            and not self.extra_accounts
            and self.source_total == self.rebuilt_total
        )

    @property
    def largest_difference(self) -> Money:
        if not self.differences:
            return Money.zero(self.currency)
        return max(
            (abs(source - rebuilt) for _id, source, rebuilt in self.differences),
            key=lambda m: m.minor_units,
        )

    def report(self) -> str:
        if self.ties:
            return (
                f"Rebuilt ledger ties to QuickBooks exactly at {self.as_of.isoformat()} "
                f"across {len(self.differences) + 1} checks."
            )
        lines = [f"Tie-out FAILED at {self.as_of.isoformat()}:"]
        if self.missing_accounts:
            lines.append(
                f"  {len(self.missing_accounts)} account(s) in QuickBooks are absent from "
                f"the rebuild: {', '.join(self.missing_accounts[:8])}"
            )
        if self.extra_accounts:
            lines.append(
                f"  {len(self.extra_accounts)} account(s) in the rebuild are absent from "
                f"QuickBooks: {', '.join(self.extra_accounts[:8])}"
            )
        for account_id, source, rebuilt in self.differences[:12]:
            lines.append(
                f"  account {account_id}: QuickBooks {source.format()}, "
                f"rebuilt {rebuilt.format()}, out by {(source - rebuilt).format()}"
            )
        return "\n".join(lines)


def compare_trial_balance(
    source: QboTrialBalance, ledger: Ledger, as_of: date, *, start: date | None = None
) -> TieOutResult:
    """Compare QuickBooks' trial balance against the one the engine rebuilds.

    This is the data gate in its strongest form. Everything the product says
    afterwards rests on these two numbers being identical.
    """
    from ..engine.trial_balance import build_trial_balance

    window_start = start or date(1900, 1, 1)
    rebuilt = build_trial_balance(ledger, window_start, as_of)
    rebuilt_by_id = {
        row.account.account_id: row.closing
        for row in rebuilt.rows
        if not row.closing.is_zero
    }
    source_by_id = {k: v for k, v in source.by_account_id().items() if not v.is_zero}

    differences: list[tuple[str, Money, Money]] = []
    for account_id, source_net in source_by_id.items():
        rebuilt_net = rebuilt_by_id.get(account_id)
        if rebuilt_net is None:
            continue
        if rebuilt_net != source_net:
            differences.append((account_id, source_net, rebuilt_net))

    missing = tuple(sorted(set(source_by_id) - set(rebuilt_by_id)))
    extra = tuple(sorted(set(rebuilt_by_id) - set(source_by_id)))

    return TieOutResult(
        as_of=as_of,
        currency=source.currency,
        source_total=msum(source_by_id.values(), source.currency),
        rebuilt_total=msum(rebuilt_by_id.values(), source.currency),
        differences=tuple(differences),
        missing_accounts=missing,
        extra_accounts=extra,
    )
