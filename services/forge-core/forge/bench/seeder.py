"""Seeded-error generator: the ground truth ForgeBench measures against.

Each injector plants one defect of a known kind, in a known place, with a known
value, and returns a :class:`SeededError` describing it. A control suite is only
credible if it is measured against defects someone deliberately hid, so the
seeder is written to be *adversarial*: several injectors plant errors that look
exactly like legitimate activity, and a few plant legitimate-but-unusual
activity that must NOT be reported.

Every injector is deterministic given a seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Iterable, Sequence

from ..canonical.enums import Severity, TxnType
from ..canonical.models import (
    Application,
    Ledger,
    Lineage,
    OpenItem,
    Transaction,
    TransactionLine,
)
from ..connectors import chart as C
from ..connectors.fixture_contractor import FixtureCompany, LedgerBuilder, build_contractor_company
from ..money import Money

__all__ = ["SeededError", "SeededCase", "INJECTORS", "seed_errors", "build_seeded_case"]


@dataclass(frozen=True)
class SeededError:
    """One deliberately planted defect and how it should be detected."""

    error_id: str
    kind: str
    description: str
    expected_rules: tuple[str, ...]
    """Rule ids that SHOULD fire. A run that misses all of them is a false negative."""
    amount: Money
    severity: Severity
    txn_ids: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()
    corroborating_rules: tuple[str, ...] = ()
    """Controls that legitimately fire as a downstream consequence of this defect.

    A missing depreciation entry should trip the variance and dormant-account
    controls as well as the depreciation control. Those are corroboration, not
    noise, and scoring them as noise would push the system toward *fewer*
    controls, which is the wrong incentive."""
    match_by_rule_only: bool = False
    """Set for a defect defined by ABSENCE (a deleted entry, a missing
    remittance) or reported at account level. There is no transaction to point
    at, so the control firing is itself the detection. Used sparingly and
    declared per error rather than applied globally."""
    is_decoy: bool = False
    """True for legitimate-but-unusual activity that must NOT be reported.
    A finding matched to a decoy counts as a false positive."""

    @property
    def is_critical(self) -> bool:
        return self.severity is Severity.CRITICAL

    @property
    def all_rules(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.expected_rules + self.corroborating_rules))


@dataclass
class SeededCase:
    """A company plus the ground truth of what was done to it."""

    company: FixtureCompany
    errors: tuple[SeededError, ...]
    period_start: date
    period_end: date
    fiscal_year_start: date
    name: str = "seeded"

    @property
    def ledger(self) -> Ledger:
        return self.company.ledger

    def planted(self, *, critical_only: bool = False) -> tuple[SeededError, ...]:
        real = tuple(e for e in self.errors if not e.is_decoy)
        return tuple(e for e in real if e.is_critical) if critical_only else real

    def decoys(self) -> tuple[SeededError, ...]:
        return tuple(e for e in self.errors if e.is_decoy)

    def expected_rule_ids(self) -> set[str]:
        out: set[str] = set()
        for e in self.planted():
            out.update(e.expected_rules)
        return out


class _Injector:
    """Wraps an injection function with its identity, for registry ordering."""

    def __init__(self, name: str, fn: Callable[..., SeededError | None]) -> None:
        self.name = name
        self.fn = fn

    def __call__(self, *args, **kwargs) -> SeededError | None:
        return self.fn(*args, **kwargs)


INJECTORS: list[_Injector] = []


def injector(name: str):
    def wrap(fn):
        INJECTORS.append(_Injector(name, fn))
        return fn

    return wrap


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _builder(ledger: Ledger) -> LedgerBuilder:
    b = LedgerBuilder(ledger.entity.entity_id, ledger.currency)
    b.transactions = ledger.transactions
    b.open_items = ledger.open_items
    b.applications = ledger.applications
    b._seq = 900_000
    return b


def _in_window(ledger: Ledger, start: date, end: date, txn_type: TxnType) -> list[Transaction]:
    return [
        t for t in ledger.transactions if t.type is txn_type and start <= t.txn_date <= end
    ]


def _money(value: str, currency: str = "CAD") -> Money:
    return Money.from_decimal(value, currency)


# ---------------------------------------------------------------------------
# injectors
# ---------------------------------------------------------------------------


@injector("duplicate_bill")
def inject_duplicate_bill(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Re-enter a vendor bill under the same document number, and pay it twice."""
    bills = [b for b in _in_window(ledger, start, end, TxnType.BILL)
             if b.absolute_value.minor_units > 400_000]
    if not bills:
        return None
    original = rng.choice(bills)
    b = _builder(ledger)
    lines = [
        b.line(l.account_id, l.amount, memo=l.memo, party_id=l.party_id, job_id=l.job_id,
               tax_code=l.tax_code, tax_amount=l.tax_amount)
        for l in original.lines
    ]
    dup = b.post(
        type=TxnType.BILL,
        txn_date=original.txn_date + timedelta(days=rng.randint(2, 9)),
        doc_number=original.doc_number,
        party_id=original.party_id,
        job_id=original.job_id,
        memo=original.memo,
        lines=lines,
        txn_id=f"SEED-DUPBILL-{original.txn_id}",
    )
    amount = original.absolute_value
    pay = b.post(
        type=TxnType.BILL_PAYMENT,
        txn_date=dup.txn_date + timedelta(days=12),
        doc_number=f"CHQ-SEEDDUP-{rng.randint(1000, 9999)}",
        party_id=original.party_id,
        memo=f"Payment of {original.doc_number}",
        lines=[b.line(C.AP, amount, party_id=original.party_id),
               b.line(C.BANK, -amount, party_id=original.party_id)],
        txn_id=f"SEED-DUPPAY-{original.txn_id}",
    )
    item = OpenItem(
        open_item_id=f"OI-SEED-DUP-{original.txn_id}", entity_id=ledger.entity.entity_id,
        txn_id=dup.txn_id, party_id=original.party_id or "", kind="payable",
        issued_on=dup.txn_date, due_on=dup.txn_date + timedelta(days=30),
        original_amount=amount, doc_number=original.doc_number,
        lineage=Lineage("forge_seeder", f"dup-{original.txn_id}"),
    )
    ledger.open_items.append(item)
    ledger.applications.append(
        Application(
            application_id=f"APP-SEED-DUP-{original.txn_id}", entity_id=ledger.entity.entity_id,
            open_item_id=item.open_item_id, payment_txn_id=pay.txn_id,
            applied_on=pay.txn_date, amount=amount,
            lineage=Lineage("forge_seeder", f"appdup-{original.txn_id}"),
        )
    )
    return SeededError(
        error_id="SEED-001",
        kind="duplicate_bill",
        description=(
            f"Bill {original.doc_number} for {amount.format()} re-entered and paid a second time"
        ),
        expected_rules=("FOS-R006",),
        corroborating_rules=("FOS-R033", "FOS-R024"),
        amount=amount,
        severity=Severity.CRITICAL,
        txn_ids=(original.txn_id, dup.txn_id, pay.txn_id),
    )


@injector("unbalanced_entry")
def inject_unbalanced_entry(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Post a journal entry whose debits do not equal its credits."""
    b = _builder(ledger)
    amount = _money("18400.00", ledger.currency)
    short = _money("1840.00", ledger.currency)
    b.post(
        type=TxnType.JOURNAL_ENTRY,
        txn_date=end - timedelta(days=4),
        doc_number="JE-SEED-OOB",
        memo="Reclassify job costs",
        is_manual=True,
        created_by="bookkeeper",
        lines=[b.line(C.MATERIALS, amount), b.line(C.AP, -(amount - short))],
        allow_unbalanced=True,
        txn_id="SEED-OOB-001",
    )
    return SeededError(
        error_id="SEED-002",
        kind="unbalanced_entry",
        description=f"Journal entry JE-SEED-OOB is out of balance by {short.format()}",
        expected_rules=("FOS-R004",),
        corroborating_rules=("FOS-R001", "FOS-R015"),
        match_by_rule_only=True,
        amount=short,
        severity=Severity.CRITICAL,
        txn_ids=("SEED-OOB-001",),
    )


@injector("closed_period_posting")
def inject_closed_period_posting(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Back-date an entry into a period that has already been closed."""
    closed = [p for p in ledger.calendar.periods if p.is_closed]
    if not closed:
        return None
    target = closed[-1]
    b = _builder(ledger)
    amount = _money("26750.00", ledger.currency)
    b.post(
        type=TxnType.JOURNAL_ENTRY,
        txn_date=target.end,
        doc_number="JE-SEED-CLOSED",
        memo="Prior year revenue adjustment",
        is_manual=True,
        created_by="bookkeeper",
        created_at=datetime.combine(end - timedelta(days=2), datetime.min.time()),
        lines=[b.line(C.AR, amount), b.line(C.REV_HARDSCAPE, -amount)],
        txn_id="SEED-CLOSED-001",
    )
    return SeededError(
        error_id="SEED-003",
        kind="closed_period_posting",
        description=(
            f"{amount.format()} of revenue back-dated into closed period {target.label}"
        ),
        expected_rules=("FOS-R011",),
        corroborating_rules=("FOS-R015", "FOS-R020", "FOS-R021"),
        amount=amount,
        severity=Severity.CRITICAL,
        txn_ids=("SEED-CLOSED-001",),
        account_ids=(C.REV_HARDSCAPE,),
    )


@injector("missing_bank_entry")
def inject_missing_bank_entry(company: FixtureCompany, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Add a bank withdrawal to the statement that was never recorded in the books."""
    from ..engine.reconcile import BankStatement, StatementLine

    target = None
    for idx, st in enumerate(company.statements):
        if st.bank_account_id == "BANK-CHQ" and st.start <= end <= st.end:
            target = idx
            break
    if target is None:
        return None
    st = company.statements[target]
    amount = _money("-9875.00", company.ledger.currency)
    line = StatementLine(
        statement_line_id="ST-SEED-MISSING",
        posted_on=st.end - timedelta(days=6),
        amount=amount,
        description="EFT WITHDRAWAL - UNRECORDED",
        reference="EFT99231",
    )
    company.statements[target] = BankStatement(
        bank_account_id=st.bank_account_id,
        start=st.start,
        end=st.end,
        opening_balance=st.opening_balance,
        closing_balance=st.closing_balance + amount,
        lines=st.lines + (line,),
    )
    return SeededError(
        error_id="SEED-004",
        kind="missing_bank_entry",
        description=(
            f"{abs(amount).format()} left the bank account with no entry in the ledger"
        ),
        expected_rules=("FOS-R008",),
        match_by_rule_only=True,
        amount=abs(amount),
        severity=Severity.CRITICAL,
        account_ids=(C.BANK, "BANK-CHQ"),
    )


@injector("unapplied_receipt")
def inject_unapplied_receipt(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Record a customer payment without applying it to any invoice."""
    b = _builder(ledger)
    amount = _money("21450.00", ledger.currency)
    customer = "CUST-02"
    b.post(
        type=TxnType.PAYMENT_RECEIVED,
        txn_date=end - timedelta(days=9),
        doc_number="RCPT-SEED-UNAPPLIED",
        party_id=customer,
        memo="Payment on account",
        lines=[b.line(C.BANK, amount, party_id=customer), b.line(C.AR, -amount, party_id=customer)],
        txn_id="SEED-UNAPPLIED-001",
    )
    return SeededError(
        error_id="SEED-005",
        kind="unapplied_receipt",
        description=f"{amount.format()} received from a customer and never applied to an invoice",
        expected_rules=("FOS-R028",),
        amount=amount,
        severity=Severity.HIGH,
        txn_ids=("SEED-UNAPPLIED-001",),
    )


@injector("payment_without_bill")
def inject_payment_without_bill(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Pay a vendor with no bill behind it, bypassing approval entirely."""
    b = _builder(ledger)
    amount = _money("34200.00", ledger.currency)
    vendor = "VEND-03"
    b.post(
        type=TxnType.BILL_PAYMENT,
        txn_date=end - timedelta(days=11),
        doc_number="CHQ-SEED-NOBILL",
        party_id=vendor,
        memo="Progress payment",
        lines=[b.line(C.AP, amount, party_id=vendor), b.line(C.BANK, -amount, party_id=vendor)],
        txn_id="SEED-NOBILL-001",
    )
    return SeededError(
        error_id="SEED-006",
        kind="payment_without_bill",
        description=f"{amount.format()} paid to a subcontractor with no bill and no approval",
        expected_rules=("FOS-R032",),
        amount=amount,
        severity=Severity.CRITICAL,
        txn_ids=("SEED-NOBILL-001",),
    )


@injector("capitalisable_expense")
def inject_capitalisable_expense(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Expense a large equipment purchase to overheads instead of capitalising it."""
    b = _builder(ledger)
    net = _money("58000.00", ledger.currency)
    hst = net.scale(Decimal("0.13"))
    b.post(
        type=TxnType.BILL,
        txn_date=end - timedelta(days=16),
        doc_number="BILL-SEED-CAPEX",
        party_id="VEND-10",
        memo="Skid steer loader purchase",
        lines=[
            b.line(C.REPAIRS, net, party_id="VEND-10", tax_code="HST-ON", tax_amount=hst),
            b.line(C.HST, hst),
            b.line(C.AP, -(net + hst), party_id="VEND-10"),
        ],
        txn_id="SEED-CAPEX-001",
    )
    return SeededError(
        error_id="SEED-007",
        kind="capitalisable_expense",
        description=f"{net.format()} equipment purchase expensed to repairs instead of capitalised",
        expected_rules=("FOS-R019",),
        corroborating_rules=("FOS-R033", "FOS-R020", "FOS-R021", "FOS-R024"),
        amount=net,
        severity=Severity.HIGH,
        txn_ids=("SEED-CAPEX-001",),
        account_ids=(C.REPAIRS, "VEND-10"),
    )


@injector("untaxed_revenue")
def inject_untaxed_revenue(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Invoice a taxable supply with no sales tax applied."""
    b = _builder(ledger)
    net = _money("47500.00", ledger.currency)
    customer = "CUST-05"
    txn = b.post(
        type=TxnType.INVOICE,
        txn_date=end - timedelta(days=13),
        doc_number="INV-SEED-NOTAX",
        party_id=customer,
        memo="Lot resurfacing - progress billing",
        lines=[
            b.line(C.AR, net, party_id=customer),
            b.line(C.REV_HARDSCAPE, -net, party_id=customer),
        ],
        txn_id="SEED-NOTAX-001",
    )
    ledger.open_items.append(
        OpenItem(
            open_item_id="OI-SEED-NOTAX", entity_id=ledger.entity.entity_id, txn_id=txn.txn_id,
            party_id=customer, kind="receivable", issued_on=txn.txn_date,
            due_on=txn.txn_date + timedelta(days=30), original_amount=net,
            doc_number="INV-SEED-NOTAX", lineage=Lineage("forge_seeder", "notax"),
        )
    )
    tax = net.scale(Decimal("0.13"))
    return SeededError(
        error_id="SEED-008",
        kind="untaxed_revenue",
        description=f"{net.format()} taxable supply invoiced without sales tax ({tax.format()} at risk)",
        expected_rules=("FOS-R035",),
        corroborating_rules=("FOS-R036",),
        amount=tax,
        severity=Severity.HIGH,
        txn_ids=("SEED-NOTAX-001",),
        account_ids=(C.HST,),
    )


@injector("unremitted_payroll")
def inject_unremitted_payroll(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Skip a source-deduction remittance so the trust liability ages."""
    target = None
    for txn in ledger.transactions:
        if (
            txn.type is TxnType.EXPENSE
            and txn.doc_number
            and txn.doc_number.startswith("CRA-")
            and start - timedelta(days=95) <= txn.txn_date <= end
        ):
            target = txn
    if target is None:
        return None
    ledger.transactions.remove(target)
    return SeededError(
        error_id="SEED-009",
        kind="unremitted_payroll",
        description=(
            f"Source deduction remittance of {target.absolute_value.format()} "
            f"({target.doc_number}) was never made"
        ),
        expected_rules=("FOS-R038",),
        match_by_rule_only=True,
        amount=target.absolute_value,
        severity=Severity.CRITICAL,
        txn_ids=(target.txn_id,),
        account_ids=(C.PAYROLL_LIAB,),
    )


@injector("missing_depreciation")
def inject_missing_depreciation(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Stop posting depreciation, overstating profit and net book value."""
    removed = [
        t
        for t in ledger.transactions
        if t.doc_number and t.doc_number.startswith("JE-DEP-") and start <= t.txn_date <= end
    ]
    if not removed:
        return None
    total = Money.zero(ledger.currency)
    for t in removed:
        ledger.transactions.remove(t)
        total = total + t.absolute_value
    return SeededError(
        error_id="SEED-010",
        kind="missing_depreciation",
        description=f"Depreciation of {total.format()} was not posted for the period",
        expected_rules=("FOS-R042",),
        corroborating_rules=("FOS-R020", "FOS-R021", "FOS-R022"),
        match_by_rule_only=True,
        amount=total,
        severity=Severity.HIGH,
        txn_ids=tuple(t.txn_id for t in removed),
        account_ids=(C.DEPRECIATION, C.ACCUM_DEP),
    )


@injector("unreversed_accrual")
def inject_unreversed_accrual(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Accrue a cost in the prior period and never reverse it, double-counting it."""
    b = _builder(ledger)
    amount = _money("23800.00", ledger.currency)
    accrual_date = start - timedelta(days=1)
    b.post(
        type=TxnType.JOURNAL_ENTRY,
        txn_date=accrual_date,
        doc_number="JE-SEED-ACCRUAL",
        memo="Accrue subcontractor costs",
        is_manual=True,
        is_adjusting=True,
        created_by="close_process",
        lines=[b.line(C.SUBCONTRACTORS, amount), b.line(C.ACCRUED, -amount)],
        txn_id="SEED-ACCRUAL-001",
    )
    return SeededError(
        error_id="SEED-011",
        kind="unreversed_accrual",
        description=f"Prior-period accrual of {amount.format()} was never reversed",
        expected_rules=("FOS-R044",),
        amount=amount,
        severity=Severity.HIGH,
        txn_ids=("SEED-ACCRUAL-001",),
    )


@injector("loan_split_error")
def inject_loan_split_error(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Code an entire loan payment to principal, understating interest expense."""
    targets = [
        t
        for t in ledger.transactions
        if t.doc_number and t.doc_number.startswith("LOAN-") and start <= t.txn_date <= end
    ]
    if not targets:
        return None
    original = targets[0]
    idx = ledger.transactions.index(original)
    payment = original.absolute_value
    interest = Money.zero(ledger.currency)
    for line in original.lines:
        if line.account_id == C.INTEREST:
            interest = line.amount
    b = _builder(ledger)
    replacement = Transaction(
        txn_id=original.txn_id,
        entity_id=original.entity_id,
        type=original.type,
        txn_date=original.txn_date,
        lines=(
            b.line(C.LOAN, payment, memo="Loan payment"),
            b.line(C.BANK, -payment),
        ),
        currency=original.currency,
        doc_number=original.doc_number,
        memo=original.memo,
        created_at=original.created_at,
        created_by=original.created_by,
        lineage=original.lineage,
    )
    ledger.transactions[idx] = replacement
    return SeededError(
        error_id="SEED-012",
        kind="loan_split_error",
        description=(
            f"Loan payment {original.doc_number} coded entirely to principal; "
            f"{interest.format()} of interest expense is missing"
        ),
        expected_rules=("FOS-R045", "FOS-R047"),
        match_by_rule_only=True,
        amount=interest,
        severity=Severity.HIGH,
        txn_ids=(original.txn_id,),
        account_ids=(C.LOAN, C.INTEREST),
    )


@injector("suspense_account")
def inject_suspense_account(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Park an unexplained amount in an 'Ask My Accountant' account."""
    from ..canonical.enums import AccountSubtype, AccountType
    from ..canonical.models import Account

    acct_id = "1999"
    ledger.accounts[acct_id] = Account(
        account_id=acct_id, entity_id=ledger.entity.entity_id, number=acct_id,
        name="Ask My Accountant", type=AccountType.ASSET,
        subtype=AccountSubtype.OTHER_CURRENT_ASSET, currency=ledger.currency,
    )
    b = _builder(ledger)
    amount = _money("31600.00", ledger.currency)
    b.post(
        type=TxnType.EXPENSE,
        txn_date=end - timedelta(days=19),
        doc_number="EXP-SEED-SUSPENSE",
        memo="Unidentified withdrawal",
        lines=[b.line(acct_id, amount), b.line(C.BANK, -amount)],
        txn_id="SEED-SUSPENSE-001",
    )
    return SeededError(
        error_id="SEED-013",
        kind="suspense_account",
        description=f"{amount.format()} parked in an unresolved holding account",
        expected_rules=("FOS-R018",),
        amount=amount,
        severity=Severity.HIGH,
        txn_ids=("SEED-SUSPENSE-001",),
        account_ids=(acct_id,),
    )


@injector("retained_earnings_posting")
def inject_retained_earnings_posting(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Post directly to retained earnings, silently restating prior results."""
    b = _builder(ledger)
    amount = _money("44000.00", ledger.currency)
    b.post(
        type=TxnType.JOURNAL_ENTRY,
        txn_date=end - timedelta(days=7),
        doc_number="JE-SEED-RE",
        memo="Prior year true-up",
        is_manual=True,
        created_by="bookkeeper",
        lines=[b.line(C.RETAINED_EARNINGS, amount), b.line(C.AP, -amount)],
        txn_id="SEED-RE-001",
    )
    return SeededError(
        error_id="SEED-014",
        kind="retained_earnings_posting",
        description=f"{amount.format()} posted directly to retained earnings",
        expected_rules=("FOS-R003",),
        corroborating_rules=("FOS-R014", "FOS-R015"),
        amount=amount,
        severity=Severity.HIGH,
        txn_ids=("SEED-RE-001",),
    )


@injector("unauthorised_poster")
def inject_unauthorised_poster(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """Post entries from an actor outside the authorised list."""
    b = _builder(ledger)
    amount = _money("16900.00", ledger.currency)
    b.post(
        type=TxnType.JOURNAL_ENTRY,
        txn_date=end - timedelta(days=5),
        doc_number="JE-SEED-ACTOR",
        memo="Adjustment",
        is_manual=True,
        created_by="temp_contractor_login",
        lines=[b.line(C.OFFICE, amount), b.line(C.VISA, -amount)],
        txn_id="SEED-ACTOR-001",
    )
    return SeededError(
        error_id="SEED-015",
        kind="unauthorised_poster",
        description=f"{amount.format()} posted by an actor not on the authorised list",
        expected_rules=("FOS-R015",),
        corroborating_rules=("FOS-R020", "FOS-R021"),
        amount=amount,
        severity=Severity.HIGH,
        txn_ids=("SEED-ACTOR-001",),
        account_ids=(C.OFFICE,),
    )


# ---- decoys: legitimate activity that must NOT be reported -----------------


@injector("decoy_large_legitimate_job")
def inject_decoy_large_job(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """A genuinely large, fully documented, correctly taxed and job-costed invoice.

    This is the shape of transaction a naive threshold-based system reports every
    time. Reporting it counts against the false-positive rate.
    """
    b = _builder(ledger)
    net = _money("96000.00", ledger.currency)
    hst = net.scale(Decimal("0.13"))
    customer = "CUST-01"
    txn = b.post(
        type=TxnType.INVOICE,
        txn_date=end - timedelta(days=12),
        doc_number="INV-DECOY-LARGE",
        party_id=customer,
        job_id="JOB-01",
        memo="Meridian Plaza phase 2 completion billing",
        lines=[
            b.line(C.AR, net + hst, party_id=customer, job_id="JOB-01"),
            b.line(C.REV_HARDSCAPE, -net, party_id=customer, job_id="JOB-01",
                   tax_code="HST-ON", tax_amount=hst),
            b.line(C.HST, -hst),
        ],
        document_refs=("vault://forge_fixture/doc/INV-DECOY-LARGE.pdf",),
        txn_id="DECOY-LARGE-001",
    )
    item = OpenItem(
        open_item_id="OI-DECOY-LARGE", entity_id=ledger.entity.entity_id, txn_id=txn.txn_id,
        party_id=customer, kind="receivable", issued_on=txn.txn_date,
        due_on=txn.txn_date + timedelta(days=30), original_amount=net + hst,
        doc_number="INV-DECOY-LARGE", job_id="JOB-01",
        lineage=Lineage("forge_seeder", "decoy-large"),
    )
    ledger.open_items.append(item)
    pay = b.post(
        type=TxnType.PAYMENT_RECEIVED,
        txn_date=end - timedelta(days=3),
        doc_number="RCPT-DECOY-LARGE",
        party_id=customer,
        memo="Payment received on INV-DECOY-LARGE",
        lines=[b.line(C.BANK, net + hst, party_id=customer),
               b.line(C.AR, -(net + hst), party_id=customer)],
        txn_id="DECOY-LARGEPAY-001",
    )
    ledger.applications.append(
        Application(
            application_id="APP-DECOY-LARGE", entity_id=ledger.entity.entity_id,
            open_item_id=item.open_item_id, payment_txn_id=pay.txn_id,
            applied_on=pay.txn_date, amount=net + hst,
            lineage=Lineage("forge_seeder", "app-decoy-large"),
        )
    )
    return SeededError(
        error_id="DECOY-001",
        kind="decoy_large_legitimate_job",
        description="A large, documented, taxed, job-costed and fully paid invoice",
        expected_rules=(),
        amount=net,
        severity=Severity.INFO,
        txn_ids=("DECOY-LARGE-001", "DECOY-LARGEPAY-001"),
        is_decoy=True,
    )


@injector("decoy_recurring_rent_increase")
def inject_decoy_rent_increase(ledger: Ledger, rng: random.Random, start: date, end: date) -> SeededError | None:
    """A contractual rent increase with the lease amendment attached.

    Legitimate, explained, and below the deviation threshold once the contract is
    considered. A system that fires here is training its user to ignore it.
    """
    for txn in ledger.transactions:
        if (
            txn.doc_number
            and txn.doc_number.startswith("RENT-")
            and start <= txn.txn_date <= end
        ):
            idx = ledger.transactions.index(txn)
            b = _builder(ledger)
            net = _money("3990.00", ledger.currency)
            hst = net.scale(Decimal("0.13"))
            ledger.transactions[idx] = Transaction(
                txn_id=txn.txn_id, entity_id=txn.entity_id, type=txn.type,
                txn_date=txn.txn_date,
                lines=(
                    b.line(C.RENT, net, party_id="VEND-07", tax_code="HST-ON", tax_amount=hst),
                    b.line(C.HST, hst),
                    b.line(C.BANK, -(net + hst)),
                ),
                currency=txn.currency, doc_number=txn.doc_number,
                memo="Yard and shop rent - 5% contractual escalation",
                party_id="VEND-07", created_at=txn.created_at, created_by=txn.created_by,
                document_refs=("vault://forge_fixture/doc/LEASE-AMENDMENT-2026.pdf",),
                lineage=txn.lineage,
            )
            return SeededError(
                error_id="DECOY-002",
                kind="decoy_recurring_rent_increase",
                description="Contractual 5% rent escalation with the lease amendment attached",
                expected_rules=(),
                amount=net - _money("3800.00", ledger.currency),
                severity=Severity.INFO,
                txn_ids=(txn.txn_id,),
                is_decoy=True,
            )
    return None


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def seed_errors(
    company: FixtureCompany,
    *,
    seed: int = 7,
    start: date,
    end: date,
    only: Sequence[str] | None = None,
) -> tuple[SeededError, ...]:
    """Apply every injector (or a named subset) and return the ground truth."""
    rng = random.Random(seed)
    wanted = set(only) if only else None
    errors: list[SeededError] = []
    for inj in INJECTORS:
        if wanted is not None and inj.name not in wanted:
            continue
        # Injectors that need the statements take the company; the rest take the ledger.
        if inj.name == "missing_bank_entry":
            result = inj(company, rng, start, end)
        else:
            result = inj(company.ledger, rng, start, end)
        if result is not None:
            errors.append(result)
    company.ledger.build_indexes()
    return tuple(errors)


def build_seeded_case(
    *,
    seed: int = 20260915,
    error_seed: int = 7,
    period_start: date = date(2026, 6, 1),
    period_end: date = date(2026, 6, 30),
    only: Sequence[str] | None = None,
    name: str = "contractor-seeded",
) -> SeededCase:
    """Build a fresh contractor company and plant the full error set in it."""
    company = build_contractor_company(seed=seed)
    errors = seed_errors(
        company, seed=error_seed, start=period_start, end=period_end, only=only
    )
    return SeededCase(
        company=company,
        errors=errors,
        period_start=period_start,
        period_end=period_end,
        fiscal_year_start=date(period_end.year, 1, 1),
        name=name,
    )
