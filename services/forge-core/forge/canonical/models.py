"""The Forge canonical financial model.

These are plain, immutable-by-convention dataclasses rather than ORM rows. The
deterministic engine and every control operate on *these* types, which means:

* the financial math is testable with no database at all;
* a connector's job is finished when it produces canonical records;
* swapping the persistence layer never changes a single accounting rule.

Every normalized record carries :class:`Lineage`. Constitution rule #1 says no
material recommendation exists without traceable evidence, and that is only
enforceable if every row can name the raw payload it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from ..money import Money
from .enums import AccountSubtype, AccountType, PartyType, Side, TxnType

__all__ = [
    "Lineage",
    "Tenant",
    "LegalEntity",
    "FiscalCalendar",
    "Period",
    "Account",
    "Party",
    "Job",
    "BankAccount",
    "DebtInstrument",
    "TransactionLine",
    "Transaction",
    "OpenItem",
    "Application",
    "DocumentEvidence",
    "Ledger",
]


@dataclass(frozen=True)
class Lineage:
    """Where a canonical record came from, and how it was produced.

    ``raw_ref`` is the evidence-vault key of the exact payload that produced this
    record; ``raw_checksum`` pins the bytes. ``mapping_version`` lets a historical
    conclusion be re-explained after a mapping change (blueprint 7.2).
    """

    source_system: str
    source_id: str
    raw_ref: str | None = None
    raw_checksum: str | None = None
    mapping_version: str = "1"
    synced_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.source_system}:{self.source_id}"


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    name: str
    home_currency: str = "CAD"
    jurisdiction: str = "CA-ON"


@dataclass(frozen=True)
class LegalEntity:
    entity_id: str
    tenant_id: str
    name: str
    currency: str = "CAD"
    jurisdiction: str = "CA-ON"
    business_number: str | None = None


@dataclass(frozen=True)
class Period:
    """An accounting period. ``is_closed`` gates post-close controls."""

    period_id: str
    entity_id: str
    start: date
    end: date
    is_closed: bool = False
    closed_at: datetime | None = None

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True)
class FiscalCalendar:
    entity_id: str
    fiscal_year_end_month: int = 12
    fiscal_year_end_day: int = 31
    periods: tuple[Period, ...] = ()

    def period_for(self, d: date) -> Period | None:
        for p in self.periods:
            if p.contains(d):
                return p
        return None


@dataclass(frozen=True)
class Account:
    """A chart-of-accounts node."""

    account_id: str
    entity_id: str
    number: str
    name: str
    type: AccountType
    subtype: AccountSubtype
    currency: str = "CAD"
    parent_id: str | None = None
    active: bool = True
    is_control_account: bool = False
    """True for AR/AP/bank control accounts, which should only move via
    subledger documents rather than free-hand journal entries."""
    tax_code: str | None = None
    opened_on: date | None = None
    lineage: Lineage | None = None

    @property
    def normal_balance(self) -> Side:
        if self.subtype is AccountSubtype.ACCUMULATED_DEPRECIATION:
            return Side.CREDIT  # contra-asset
        if self.subtype is AccountSubtype.OWNER_DRAWS:
            return Side.DEBIT  # contra-equity
        return self.type.normal_balance

    @property
    def is_contra(self) -> bool:
        return self.normal_balance is not self.type.normal_balance


@dataclass(frozen=True)
class Party:
    party_id: str
    entity_id: str
    name: str
    type: PartyType
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    payment_terms_days: int = 30
    active: bool = True
    bank_account_fingerprint: str | None = None
    """Hash of the vendor's remittance details. A change here is the single
    highest-value fraud signal in AP; it is never stored in the clear."""
    created_on: date | None = None
    lineage: Lineage | None = None


@dataclass(frozen=True)
class Job:
    """A project/job. The unit of profitability for a trades business."""

    job_id: str
    entity_id: str
    name: str
    customer_id: str | None = None
    contract_value: Money | None = None
    started_on: date | None = None
    completed_on: date | None = None
    status: str = "active"
    lineage: Lineage | None = None


@dataclass(frozen=True)
class BankAccount:
    bank_account_id: str
    entity_id: str
    account_id: str
    name: str
    institution: str
    masked_number: str
    currency: str = "CAD"
    is_credit_card: bool = False
    lineage: Lineage | None = None


@dataclass(frozen=True)
class DebtInstrument:
    debt_id: str
    entity_id: str
    name: str
    lender: str
    liability_account_id: str
    interest_account_id: str | None
    original_principal: Money | None = None
    annual_rate: Decimal | None = None
    started_on: date | None = None
    matures_on: date | None = None
    scheduled_payment: Money | None = None
    lineage: Lineage | None = None


@dataclass(frozen=True)
class TransactionLine:
    """One side of a double entry.

    ``amount`` is *signed in debit-positive convention*: a positive amount is a
    debit, a negative amount is a credit. One convention, enforced everywhere,
    removes an entire class of sign bugs. Use :attr:`side` to read it back.
    """

    line_id: str
    account_id: str
    amount: Money
    memo: str | None = None
    party_id: str | None = None
    job_id: str | None = None
    tax_code: str | None = None
    tax_amount: Money | None = None
    quantity: Decimal | None = None
    item: str | None = None
    line_number: int = 0

    @property
    def side(self) -> Side:
        return Side.DEBIT if self.amount.minor_units >= 0 else Side.CREDIT

    @property
    def debit(self) -> Money:
        return self.amount if self.amount.minor_units > 0 else Money.zero(self.amount.currency)

    @property
    def credit(self) -> Money:
        return -self.amount if self.amount.minor_units < 0 else Money.zero(self.amount.currency)


@dataclass(frozen=True)
class Transaction:
    """A balanced set of lines posted on one date by one source document."""

    txn_id: str
    entity_id: str
    type: TxnType
    txn_date: date
    lines: tuple[TransactionLine, ...]
    currency: str = "CAD"
    doc_number: str | None = None
    memo: str | None = None
    party_id: str | None = None
    job_id: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    last_modified_at: datetime | None = None
    posted_to_period_id: str | None = None
    is_manual: bool = False
    is_adjusting: bool = False
    reverses_txn_id: str | None = None
    document_refs: tuple[str, ...] = ()
    lineage: Lineage | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def total_debits(self) -> Money:
        total = Money.zero(self.currency)
        for ln in self.lines:
            total = total + ln.debit
        return total

    @property
    def total_credits(self) -> Money:
        total = Money.zero(self.currency)
        for ln in self.lines:
            total = total + ln.credit
        return total

    @property
    def imbalance(self) -> Money:
        """Debits minus credits. Must be zero for a valid entry."""
        return self.total_debits - self.total_credits

    @property
    def is_balanced(self) -> bool:
        return self.imbalance.is_zero

    @property
    def absolute_value(self) -> Money:
        """Gross size of the entry, used for materiality and risk scoring."""
        return self.total_debits

    def lines_for(self, account_ids: Sequence[str]) -> tuple[TransactionLine, ...]:
        wanted = set(account_ids)
        return tuple(ln for ln in self.lines if ln.account_id in wanted)


@dataclass(frozen=True)
class OpenItem:
    """An AR invoice or AP bill that can be paid down over time."""

    open_item_id: str
    entity_id: str
    txn_id: str
    party_id: str
    kind: str  # "receivable" | "payable"
    issued_on: date
    due_on: date
    original_amount: Money
    doc_number: str | None = None
    job_id: str | None = None
    lineage: Lineage | None = None

    def days_past_due(self, as_of: date) -> int:
        return (as_of - self.due_on).days


@dataclass(frozen=True)
class Application:
    """A payment/credit applied against an open item."""

    application_id: str
    entity_id: str
    open_item_id: str
    payment_txn_id: str
    applied_on: date
    amount: Money
    lineage: Lineage | None = None


@dataclass(frozen=True)
class DocumentEvidence:
    """A source document (invoice PDF, bank statement, contract) in the vault."""

    document_id: str
    entity_id: str
    filename: str
    media_type: str
    vault_ref: str
    checksum: str
    uploaded_at: datetime | None = None
    extracted: Mapping[str, Any] = field(default_factory=dict)
    linked_txn_ids: tuple[str, ...] = ()
    """Extracted fields are DATA, never instructions. The agent layer wraps this
    content in an untrusted-content envelope before it reaches any model."""


@dataclass
class Ledger:
    """An in-memory, entity-scoped book of record for one analysis run.

    This is the object every control receives. It is deliberately a simple
    container with pre-built indexes: controls must stay cheap and pure so that
    fifty of them can run over a year of data in well under a second.
    """

    tenant: Tenant
    entity: LegalEntity
    calendar: FiscalCalendar
    accounts: dict[str, Account] = field(default_factory=dict)
    parties: dict[str, Party] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    bank_accounts: dict[str, BankAccount] = field(default_factory=dict)
    debts: dict[str, DebtInstrument] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)
    open_items: list[OpenItem] = field(default_factory=list)
    applications: list[Application] = field(default_factory=list)
    documents: dict[str, DocumentEvidence] = field(default_factory=dict)

    # ---- indexes (built once, read many) ------------------------------

    _by_account: dict[str, list[tuple[Transaction, TransactionLine]]] | None = None
    _applications_by_item: dict[str, list[Application]] | None = None

    @property
    def currency(self) -> str:
        return self.entity.currency

    def build_indexes(self) -> "Ledger":
        by_account: dict[str, list[tuple[Transaction, TransactionLine]]] = {}
        for txn in self.transactions:
            for ln in txn.lines:
                by_account.setdefault(ln.account_id, []).append((txn, ln))
        self._by_account = by_account
        by_item: dict[str, list[Application]] = {}
        for app in self.applications:
            by_item.setdefault(app.open_item_id, []).append(app)
        self._applications_by_item = by_item
        return self

    def postings(self, account_id: str) -> list[tuple[Transaction, TransactionLine]]:
        if self._by_account is None:
            self.build_indexes()
        assert self._by_account is not None
        return self._by_account.get(account_id, [])

    def applications_for(self, open_item_id: str) -> list[Application]:
        if self._applications_by_item is None:
            self.build_indexes()
        assert self._applications_by_item is not None
        return self._applications_by_item.get(open_item_id, [])

    def accounts_of(
        self, *, type: AccountType | None = None, subtype: AccountSubtype | None = None
    ) -> list[Account]:
        out = []
        for acct in self.accounts.values():
            if type is not None and acct.type is not type:
                continue
            if subtype is not None and acct.subtype is not subtype:
                continue
            out.append(acct)
        return sorted(out, key=lambda a: (a.number, a.account_id))

    def account(self, account_id: str) -> Account | None:
        return self.accounts.get(account_id)

    def transactions_in(self, period: Period) -> list[Transaction]:
        return [t for t in self.transactions if period.contains(t.txn_date)]

    def transactions_between(self, start: date, end: date) -> list[Transaction]:
        return [t for t in self.transactions if start <= t.txn_date <= end]
