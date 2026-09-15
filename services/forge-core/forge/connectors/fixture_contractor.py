"""A deterministic synthetic contractor company.

Why this exists: you cannot build trustworthy finance software against a demo
file with twelve transactions in it. ForgeBench needs a book that behaves like a
real trades business - seasonal revenue, semi-monthly payroll, a loan, a credit
card, sales tax, deposits, jobs and timing differences - so that a control which
passes here has actually been tested against the mess it will meet in production.

The generator is seeded and pure: the same seed always produces byte-identical
output, which is what makes regression testing meaningful. Out of the box it
produces a *clean, balanced, tie-ing* book. Errors are injected separately by
:mod:`forge.bench.seeder` so that every finding has a known ground truth.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Sequence

from ..canonical.enums import PartyType, TxnType
from ..canonical.models import (
    Application,
    BankAccount,
    DebtInstrument,
    FiscalCalendar,
    Job,
    Ledger,
    LegalEntity,
    Lineage,
    OpenItem,
    Party,
    Period,
    Tenant,
    Transaction,
    TransactionLine,
)
from ..engine.reconcile import BankStatement, StatementLine
from ..money import Money
from . import chart as C

HST_RATE = Decimal("0.13")
SOURCE = "forge_fixture"

__all__ = ["FixtureCompany", "build_contractor_company", "LedgerBuilder"]


def _d(value: str) -> Money:
    return Money.from_decimal(value, "CAD")


class LedgerBuilder:
    """Accumulates balanced transactions and refuses to post an unbalanced one."""

    def __init__(self, entity_id: str, currency: str = "CAD") -> None:
        self.entity_id = entity_id
        self.currency = currency
        self.transactions: list[Transaction] = []
        self.open_items: list[OpenItem] = []
        self.applications: list[Application] = []
        self._seq = 0

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:06d}"

    def line(
        self,
        account_id: str,
        amount: Money,
        *,
        memo: str | None = None,
        party_id: str | None = None,
        job_id: str | None = None,
        tax_code: str | None = None,
        tax_amount: Money | None = None,
        line_number: int = 0,
    ) -> TransactionLine:
        return TransactionLine(
            line_id=self._next("LN"),
            account_id=account_id,
            amount=amount,
            memo=memo,
            party_id=party_id,
            job_id=job_id,
            tax_code=tax_code,
            tax_amount=tax_amount,
            line_number=line_number,
        )

    def post(
        self,
        *,
        type: TxnType,
        txn_date: date,
        lines: Sequence[TransactionLine],
        doc_number: str | None = None,
        memo: str | None = None,
        party_id: str | None = None,
        job_id: str | None = None,
        is_manual: bool = False,
        is_adjusting: bool = False,
        created_by: str = "sync",
        created_at: datetime | None = None,
        txn_id: str | None = None,
        allow_unbalanced: bool = False,
        reverses_txn_id: str | None = None,
        document_refs: Sequence[str] = (),
    ) -> Transaction:
        txn = Transaction(
            txn_id=txn_id or self._next("TXN"),
            entity_id=self.entity_id,
            type=type,
            txn_date=txn_date,
            lines=tuple(lines),
            currency=self.currency,
            doc_number=doc_number,
            memo=memo,
            party_id=party_id,
            job_id=job_id,
            created_at=created_at or datetime.combine(txn_date, datetime.min.time()),
            created_by=created_by,
            is_manual=is_manual,
            is_adjusting=is_adjusting,
            reverses_txn_id=reverses_txn_id,
            document_refs=tuple(document_refs),
            lineage=Lineage(
                source_system=SOURCE,
                source_id=txn_id or f"seq{self._seq}",
                raw_ref=f"vault://{SOURCE}/txn/{txn_id or self._seq}",
                mapping_version="1",
            ),
        )
        if not allow_unbalanced and not txn.is_balanced:
            raise AssertionError(
                f"Generator produced an unbalanced entry {txn.txn_id}: {txn.imbalance}"
            )
        self.transactions.append(txn)
        return txn


@dataclass
class FixtureCompany:
    """A generated company: its ledger, statements and the facts used to build it."""

    ledger: Ledger
    statements: list[BankStatement] = field(default_factory=list)
    period_start: date = date(2025, 1, 1)
    period_end: date = date(2026, 6, 30)
    fiscal_year_start: date = date(2026, 1, 1)

    @property
    def entity_id(self) -> str:
        return self.ledger.entity.entity_id


CUSTOMERS = [
    ("CUST-01", "Meridian Property Group", 30),
    ("CUST-02", "Oakridge Condominium Corp", 45),
    ("CUST-03", "Harbourfront Retail Holdings", 30),
    ("CUST-04", "Ravenscliff Estates HOA", 60),
    ("CUST-05", "Stonegate Industrial Park", 30),
    ("CUST-06", "Lakeview Medical Centre", 30),
    ("CUST-07", "Brookfield Residences", 45),
    ("CUST-08", "Coastal Logistics Depot", 30),
    ("CUST-09", "Willowbrook Retirement Living", 30),
    ("CUST-10", "Fairmount Business Campus", 60),
]

VENDORS = [
    ("VEND-01", "Northstone Aggregate Supply", C.MATERIALS, 30),
    ("VEND-02", "Precision Paver Distributors", C.MATERIALS, 30),
    ("VEND-03", "Halloway Excavating Ltd", C.SUBCONTRACTORS, 15),
    ("VEND-04", "Ironbridge Equipment Rentals", C.EQUIPMENT_RENTAL, 30),
    ("VEND-05", "Summit Fuel and Fleet", C.VEHICLE, 15),
    ("VEND-06", "Delacroix Insurance Brokers", C.INSURANCE, 30),
    ("VEND-07", "Kestrel Yard Leasing", C.RENT, 1),
    ("VEND-08", "Ashford Accounting Services", C.PROFESSIONAL, 30),
    ("VEND-09", "Granite Marketing Co", C.ADVERTISING, 30),
    ("VEND-10", "Cedarline Hardware", C.REPAIRS, 30),
]

JOBS = [
    ("JOB-01", "Meridian Plaza Hardscape Rebuild", "CUST-01", "185000.00"),
    ("JOB-02", "Oakridge Walkway Replacement", "CUST-02", "92000.00"),
    ("JOB-03", "Harbourfront Seasonal Snow Contract", "CUST-03", "64000.00"),
    ("JOB-04", "Ravenscliff Retaining Wall", "CUST-04", "138000.00"),
    ("JOB-05", "Stonegate Lot Resurfacing", "CUST-05", "210000.00"),
    ("JOB-06", "Lakeview Grounds Maintenance", "CUST-06", "48000.00"),
    ("JOB-07", "Brookfield Courtyard Build", "CUST-07", "156000.00"),
    ("JOB-08", "Coastal Depot Snow and Ice", "CUST-08", "78000.00"),
]

# Revenue mix by month: (hardscape weight, snow weight, maintenance weight).
SEASONALITY: dict[int, tuple[int, int, int]] = {
    1: (5, 80, 15), 2: (5, 78, 17), 3: (20, 45, 35), 4: (55, 5, 40),
    5: (70, 0, 30), 6: (75, 0, 25), 7: (78, 0, 22), 8: (76, 0, 24),
    9: (70, 0, 30), 10: (55, 10, 35), 11: (25, 50, 25), 12: (8, 72, 20),
}

MONTH_VOLUME: dict[int, tuple[int, int]] = {
    1: (7, 10), 2: (7, 10), 3: (6, 9), 4: (9, 13), 5: (11, 15), 6: (12, 16),
    7: (12, 16), 8: (11, 15), 9: (10, 14), 10: (9, 13), 11: (8, 11), 12: (7, 10),
}


def _month_iter(start: date, end: date) -> Iterable[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + (m == 12), (m % 12) + 1)


def _month_end(y: int, m: int) -> date:
    return date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)


def _clamp_day(y: int, m: int, day: int) -> date:
    last = _month_end(y, m).day
    return date(y, m, min(day, last))


def build_contractor_company(
    *,
    seed: int = 20260915,
    start: date = date(2025, 1, 1),
    end: date = date(2026, 6, 30),
    tenant_id: str = "TEN-NORTHLINE",
    entity_id: str = "ENT-NORTHLINE",
    name: str = "Northline Hardscape & Snow Ltd.",
) -> FixtureCompany:
    """Generate a clean, balanced 18-month book for a landscaping contractor."""
    rng = random.Random(seed)
    tenant = Tenant(tenant_id=tenant_id, name=name, home_currency="CAD", jurisdiction="CA-ON")
    entity = LegalEntity(
        entity_id=entity_id,
        tenant_id=tenant_id,
        name=name,
        currency="CAD",
        jurisdiction="CA-ON",
        business_number="81234 5678 RT0001",
    )
    accounts = C.build_chart(entity_id)
    b = LedgerBuilder(entity_id)

    parties: dict[str, Party] = {}
    for pid, pname, terms in CUSTOMERS:
        parties[pid] = Party(
            party_id=pid, entity_id=entity_id, name=pname, type=PartyType.CUSTOMER,
            payment_terms_days=terms, email=f"ap@{pname.split()[0].lower()}.example",
            created_on=start - timedelta(days=rng.randint(200, 900)),
            lineage=Lineage(SOURCE, pid, f"vault://{SOURCE}/customer/{pid}"),
        )
    for pid, pname, _acct, terms in VENDORS:
        parties[pid] = Party(
            party_id=pid, entity_id=entity_id, name=pname, type=PartyType.VENDOR,
            payment_terms_days=terms,
            bank_account_fingerprint=f"fp-{abs(hash(pid)) % 10**10:010d}",
            created_on=start - timedelta(days=rng.randint(200, 900)),
            lineage=Lineage(SOURCE, pid, f"vault://{SOURCE}/vendor/{pid}"),
        )

    jobs = {
        jid: Job(
            job_id=jid, entity_id=entity_id, name=jname, customer_id=cust,
            contract_value=_d(value), started_on=start + timedelta(days=rng.randint(0, 300)),
            lineage=Lineage(SOURCE, jid, f"vault://{SOURCE}/job/{jid}"),
        )
        for jid, jname, cust, value in JOBS
    }

    # ---- opening balances ---------------------------------------------
    opening_date = start - timedelta(days=1)
    opening_lines = [
        b.line(C.BANK, _d("84250.00"), memo="Opening balance"),
        b.line(C.TAX_SAVINGS, _d("31000.00"), memo="Opening balance"),
        b.line(C.AR, _d("96400.00"), memo="Opening balance"),
        b.line(C.PREPAID_INSURANCE, _d("7200.00"), memo="Opening balance"),
        b.line(C.EQUIPMENT, _d("412000.00"), memo="Opening balance"),
        b.line(C.VEHICLES, _d("188000.00"), memo="Opening balance"),
        b.line(C.ACCUM_DEP, _d("-214500.00"), memo="Opening balance"),
        b.line(C.AP, _d("-58300.00"), memo="Opening balance"),
        b.line(C.VISA, _d("-9450.00"), memo="Opening balance"),
        b.line(C.HST, _d("-14800.00"), memo="Opening balance"),
        b.line(C.PAYROLL_LIAB, _d("-11250.00"), memo="Opening balance"),
        b.line(C.LOAN, _d("-196000.00"), memo="Opening balance"),
        b.line(C.COMMON_SHARES, _d("-100.00"), memo="Opening balance"),
    ]
    plug = -sum_lines(opening_lines)
    opening_lines.append(b.line(C.RETAINED_EARNINGS, plug, memo="Opening retained earnings"))
    b.post(
        type=TxnType.OPENING_BALANCE, txn_date=opening_date, lines=opening_lines,
        memo="Opening balances migrated from prior system", created_by="migration",
    )

    # Opening AR detail so the subledger agrees with the control account.
    _seed_opening_ar(b, rng, entity_id, opening_date, _d("96400.00"))
    _seed_opening_ap(b, rng, entity_id, opening_date, _d("58300.00"))
    # The opening source-deduction liability is remitted on the statutory date in
    # the first month, as it would be in a compliant file.
    b.post(
        type=TxnType.EXPENSE, txn_date=date(start.year, start.month, 15),
        doc_number=f"CRA-OPEN-{start.year}", memo="CRA source deduction remittance - opening balance",
        lines=[b.line(C.PAYROLL_LIAB, _d("11250.00")), b.line(C.BANK, _d("-11250.00"))],
    )

    invoice_no = 4100
    bill_no = 1

    for y, m in _month_iter(start, end):
        m_end = _month_end(y, m)
        invoice_no, bill_no = _generate_month(
            b, rng, y, m, m_end, jobs, invoice_no, bill_no, end
        )

    ledger = Ledger(
        tenant=tenant,
        entity=entity,
        calendar=_calendar(entity_id, start, end),
        accounts=accounts,
        parties=parties,
        jobs=jobs,
        bank_accounts={
            "BANK-CHQ": BankAccount(
                bank_account_id="BANK-CHQ", entity_id=entity_id, account_id=C.BANK,
                name="Chequing - Operating", institution="Meridian Credit Union",
                masked_number="****4417",
            ),
            "BANK-VISA": BankAccount(
                bank_account_id="BANK-VISA", entity_id=entity_id, account_id=C.VISA,
                name="Visa - Operating Card", institution="Meridian Credit Union",
                masked_number="****9082", is_credit_card=True,
            ),
        },
        debts={
            "DEBT-EQUIP": DebtInstrument(
                debt_id="DEBT-EQUIP", entity_id=entity_id, name="Equipment Loan",
                lender="Meridian Credit Union", liability_account_id=C.LOAN,
                interest_account_id=C.INTEREST, original_principal=_d("260000.00"),
                annual_rate=Decimal("0.079"), started_on=date(2023, 4, 1),
                matures_on=date(2029, 4, 1), scheduled_payment=_d("4180.00"),
            )
        },
        transactions=b.transactions,
        open_items=b.open_items,
        applications=b.applications,
    ).build_indexes()

    company = FixtureCompany(
        ledger=ledger, period_start=start, period_end=end,
        fiscal_year_start=date(end.year, 1, 1),
    )
    company.statements = _build_statements(ledger, start, end)
    return company


def sum_lines(lines: Sequence[TransactionLine]) -> Money:
    total = Money.zero("CAD")
    for ln in lines:
        total = total + ln.amount
    return total


def _calendar(entity_id: str, start: date, end: date) -> FiscalCalendar:
    periods = []
    for y, m in _month_iter(start, end):
        p_start = date(y, m, 1)
        p_end = _month_end(y, m)
        periods.append(
            Period(
                period_id=f"{y:04d}-{m:02d}",
                entity_id=entity_id,
                start=p_start,
                end=p_end,
                # Everything up to three months before the end of data is closed.
                is_closed=p_end < (end.replace(day=1) - timedelta(days=62)),
            )
        )
    return FiscalCalendar(entity_id=entity_id, periods=tuple(periods))


def _seed_opening_ar(b: LedgerBuilder, rng, entity_id, opening_date, total: Money) -> None:
    """Split the opening AR balance into real invoices so aging is meaningful.

    Four of the five are collected in the opening quarter. The fifth is left
    outstanding on purpose: every real migrated file carries one stale balance
    nobody has chased, and the aging controls should see it.
    """
    picks = [c[0] for c in CUSTOMERS[:5]]
    weights = [35, 25, 18, 12, 10]
    for idx, (pid, amount) in enumerate(zip(picks, total.allocate(weights))):
        issued = opening_date - timedelta(days=rng.randint(10, 75))
        item = OpenItem(
            open_item_id=f"OI-AR-OPEN-{pid}", entity_id=entity_id,
            txn_id="TXN-000001", party_id=pid, kind="receivable",
            issued_on=issued, due_on=issued + timedelta(days=30),
            original_amount=amount, doc_number=f"OPEN-{pid}",
            lineage=Lineage(SOURCE, f"ar-open-{pid}"),
        )
        b.open_items.append(item)
        if idx == 4:
            continue
        paid_on = opening_date + timedelta(days=rng.randint(8, 70))
        pay = b.post(
            type=TxnType.PAYMENT_RECEIVED, txn_date=paid_on, party_id=pid,
            doc_number=f"RCPT-OPEN-{pid}", memo=f"Payment received on OPEN-{pid}",
            lines=[b.line(C.BANK, amount, party_id=pid), b.line(C.AR, -amount, party_id=pid)],
        )
        b.applications.append(
            Application(
                application_id=f"APP-AR-OPEN-{pid}", entity_id=entity_id,
                open_item_id=item.open_item_id, payment_txn_id=pay.txn_id,
                applied_on=paid_on, amount=amount,
                lineage=Lineage(SOURCE, f"app-ar-open-{pid}"),
            )
        )


def _seed_opening_ap(b: LedgerBuilder, rng, entity_id, opening_date, total: Money) -> None:
    """Opening payables, all settled inside the first month as they would be."""
    picks = [v[0] for v in VENDORS[:4]]
    for pid, amount in zip(picks, total.allocate([40, 28, 20, 12])):
        issued = opening_date - timedelta(days=rng.randint(5, 40))
        item = OpenItem(
            open_item_id=f"OI-AP-OPEN-{pid}", entity_id=entity_id,
            txn_id="TXN-000001", party_id=pid, kind="payable",
            issued_on=issued, due_on=issued + timedelta(days=30),
            original_amount=amount, doc_number=f"OPENAP-{pid}",
            lineage=Lineage(SOURCE, f"ap-open-{pid}"),
        )
        b.open_items.append(item)
        paid_on = opening_date + timedelta(days=rng.randint(5, 26))
        pay = b.post(
            type=TxnType.BILL_PAYMENT, txn_date=paid_on, party_id=pid,
            doc_number=f"CHQ-OPEN-{pid}", memo=f"Payment of OPENAP-{pid}",
            lines=[b.line(C.AP, amount, party_id=pid), b.line(C.BANK, -amount, party_id=pid)],
        )
        b.applications.append(
            Application(
                application_id=f"APP-AP-OPEN-{pid}", entity_id=entity_id,
                open_item_id=item.open_item_id, payment_txn_id=pay.txn_id,
                applied_on=paid_on, amount=amount,
                lineage=Lineage(SOURCE, f"app-ap-open-{pid}"),
            )
        )


def _hst_split(gross: Money) -> tuple[Money, Money]:
    """Split a tax-inclusive total into (net revenue, HST)."""
    net = gross.scale(Decimal(1) / (Decimal(1) + HST_RATE))
    return net, gross - net


def _generate_month(
    b: LedgerBuilder, rng, y: int, m: int, m_end: date, jobs, invoice_no: int,
    bill_no: int, data_end: date,
) -> tuple[int, int]:
    entity_id = b.entity_id
    hard_w, snow_w, maint_w = SEASONALITY[m]
    lo, hi = MONTH_VOLUME[m]
    n_invoices = rng.randint(lo, hi)
    month_revenue = Money.zero("CAD")

    # ---- customer invoices --------------------------------------------
    for _ in range(n_invoices):
        cust_id, _cname, terms = rng.choice(CUSTOMERS)
        stream = rng.choices(
            [C.REV_HARDSCAPE, C.REV_SNOW, C.REV_MAINTENANCE],
            weights=[max(hard_w, 1), max(snow_w, 1), max(maint_w, 1)],
        )[0]
        if stream == C.REV_HARDSCAPE:
            net = _d(f"{rng.randint(6, 36) * 1000 + rng.randint(0, 99) * 10}.00")
        elif stream == C.REV_SNOW:
            net = _d(f"{rng.randint(3, 13) * 1000 + rng.randint(0, 99) * 10}.00")
        else:
            net = _d(f"{rng.randint(1, 6) * 1000 + rng.randint(0, 99) * 10}.00")
        hst = net.scale(HST_RATE)
        gross = net + hst
        month_revenue = month_revenue + net
        issued = _clamp_day(y, m, rng.randint(2, 27))
        job = rng.choice(list(jobs.values())) if rng.random() < 0.72 else None
        invoice_no += 1
        doc = f"INV-{invoice_no}"
        txn = b.post(
            type=TxnType.INVOICE, txn_date=issued, doc_number=doc, party_id=cust_id,
            job_id=job.job_id if job else None,
            memo=f"{'Hardscape' if stream == C.REV_HARDSCAPE else 'Snow and ice' if stream == C.REV_SNOW else 'Grounds maintenance'} services",
            lines=[
                b.line(C.AR, gross, party_id=cust_id, job_id=job.job_id if job else None),
                b.line(stream, -net, party_id=cust_id, job_id=job.job_id if job else None,
                       tax_code="HST-ON", tax_amount=hst),
                b.line(C.HST, -hst, memo="HST collected on sales"),
            ],
            document_refs=(f"vault://{SOURCE}/doc/{doc}.pdf",),
        )
        item = OpenItem(
            open_item_id=f"OI-{doc}", entity_id=entity_id, txn_id=txn.txn_id,
            party_id=cust_id, kind="receivable", issued_on=issued,
            due_on=issued + timedelta(days=terms), original_amount=gross,
            doc_number=doc, job_id=job.job_id if job else None,
            lineage=Lineage(SOURCE, doc, f"vault://{SOURCE}/invoice/{doc}"),
        )
        b.open_items.append(item)

        # Most invoices get paid; a realistic tail does not.
        roll = rng.random()
        if roll < 0.965:
            lag = max(3, int(rng.gauss(terms + 5, 10)))
            paid_on = issued + timedelta(days=lag)
            if paid_on <= data_end:
                pay = b.post(
                    type=TxnType.PAYMENT_RECEIVED, txn_date=paid_on, party_id=cust_id,
                    doc_number=f"RCPT-{invoice_no}", memo=f"Payment received on {doc}",
                    lines=[
                        b.line(C.BANK, gross, party_id=cust_id),
                        b.line(C.AR, -gross, party_id=cust_id),
                    ],
                )
                b.applications.append(
                    Application(
                        application_id=f"APP-{doc}", entity_id=entity_id,
                        open_item_id=item.open_item_id, payment_txn_id=pay.txn_id,
                        applied_on=paid_on, amount=gross,
                        lineage=Lineage(SOURCE, f"app-{doc}"),
                    )
                )

    # ---- vendor bills --------------------------------------------------
    # Job costs are driven by the month's revenue, the way they are in a real
    # contractor's file. Flat random spend would produce a gross margin that
    # drifts month to month for no business reason and would make every
    # variance control fire on noise.
    cost_plan: list[tuple[str, str, Decimal, int]] = [
        ("VEND-01", C.MATERIALS, Decimal("0.168"), 3),
        ("VEND-02", C.MATERIALS, Decimal("0.112"), 2),
        ("VEND-03", C.SUBCONTRACTORS, Decimal("0.171"), 3),
        ("VEND-04", C.EQUIPMENT_RENTAL, Decimal("0.036"), 2),
    ]
    overhead_plan: list[tuple[str, str, int, int]] = [
        ("VEND-05", C.VEHICLE, 2400, 6100),
        ("VEND-08", C.PROFESSIONAL, 650, 2400),
        ("VEND-09", C.ADVERTISING, 400, 2200),
        ("VEND-10", C.REPAIRS, 300, 2900),
    ]

    bill_specs: list[tuple[str, str, Money, int]] = []
    for vend_id, expense_acct, share, splits in cost_plan:
        budget = month_revenue.scale(share * Decimal(rng.uniform(0.88, 1.12)).quantize(Decimal("0.0001")))
        if budget.minor_units < 20000:
            continue
        weights = [rng.randint(60, 140) for _ in range(splits)]
        for part in budget.allocate(weights):
            if part.minor_units < 5000:
                continue
            terms = next(t for v, _n, _a, t in VENDORS if v == vend_id)
            bill_specs.append((vend_id, expense_acct, part, terms))
    for vend_id, expense_acct, lo_amt, hi_amt in overhead_plan:
        net = _d(f"{rng.randint(lo_amt, hi_amt)}.00")
        terms = next(t for v, _n, _a, t in VENDORS if v == vend_id)
        bill_specs.append((vend_id, expense_acct, net, terms))

    for vend_id, expense_acct, net, terms in bill_specs:
        hst = net.scale(HST_RATE)
        gross = net + hst
        issued = _clamp_day(y, m, rng.randint(1, 26))
        bill_no += 1
        doc = f"BILL-{y % 100:02d}{m:02d}-{bill_no:04d}"
        job = (
            rng.choice(list(jobs.values()))
            if expense_acct in (C.MATERIALS, C.SUBCONTRACTORS, C.EQUIPMENT_RENTAL)
            and rng.random() < 0.85
            else None
        )
        txn = b.post(
            type=TxnType.BILL, txn_date=issued, doc_number=doc, party_id=vend_id,
            job_id=job.job_id if job else None, memo=f"{_vendor_name(vend_id)} - supply",
            lines=[
                b.line(expense_acct, net, party_id=vend_id,
                       job_id=job.job_id if job else None, tax_code="HST-ON", tax_amount=hst),
                b.line(C.HST, hst, memo="Input tax credit"),
                b.line(C.AP, -gross, party_id=vend_id),
            ],
            document_refs=(f"vault://{SOURCE}/doc/{doc}.pdf",),
        )
        item = OpenItem(
            open_item_id=f"OI-{doc}", entity_id=entity_id, txn_id=txn.txn_id,
            party_id=vend_id, kind="payable", issued_on=issued,
            due_on=issued + timedelta(days=terms), original_amount=gross, doc_number=doc,
            lineage=Lineage(SOURCE, doc, f"vault://{SOURCE}/bill/{doc}"),
        )
        b.open_items.append(item)
        if rng.random() < 0.94:
            paid_on = issued + timedelta(days=max(2, int(rng.gauss(terms + 4, 7))))
            if paid_on <= data_end:
                pay = b.post(
                    type=TxnType.BILL_PAYMENT, txn_date=paid_on, party_id=vend_id,
                    doc_number=f"CHQ-{6000 + bill_no}", memo=f"Payment of {doc}",
                    lines=[
                        b.line(C.AP, gross, party_id=vend_id),
                        b.line(C.BANK, -gross, party_id=vend_id),
                    ],
                )
                b.applications.append(
                    Application(
                        application_id=f"APP-{doc}", entity_id=entity_id,
                        open_item_id=item.open_item_id, payment_txn_id=pay.txn_id,
                        applied_on=paid_on, amount=gross,
                        lineage=Lineage(SOURCE, f"app-{doc}"),
                    )
                )

    # ---- payroll, semi-monthly ------------------------------------------
    for day in (15, _month_end(y, m).day):
        pay_date = date(y, m, day)
        headcount = 11 if m in (5, 6, 7, 8, 9) else 7
        gross_pay = _d(f"{headcount * rng.randint(1900, 2350)}.00")
        # Field crew wages are a cost of sales; office and management wages are
        # overhead. Splitting them is the difference between a gross margin a
        # contractor can act on and one that means nothing.
        field_pay, admin_pay = gross_pay.allocate([72, 28])
        employer_tax = gross_pay.scale(Decimal("0.0938"))
        employee_deductions = gross_pay.scale(Decimal("0.2410"))
        net_pay = gross_pay - employee_deductions
        b.post(
            type=TxnType.PAYROLL, txn_date=pay_date, doc_number=f"PR-{y}{m:02d}-{day:02d}",
            memo=f"Payroll - {headcount} employees", created_by="payroll_sync",
            lines=[
                b.line(C.DIRECT_LABOUR, field_pay, memo="Field crew wages"),
                b.line(C.WAGES, admin_pay, memo="Office and management wages"),
                b.line(C.PAYROLL_TAXES, employer_tax, memo="Employer CPP/EI"),
                b.line(C.BANK, -net_pay, memo="Net pay direct deposit"),
                b.line(C.PAYROLL_LIAB, -(employee_deductions + employer_tax),
                       memo="Source deductions payable"),
            ],
        )

    # Remittance of the prior month's source deductions on the 15th.
    remit_date = _clamp_day(y, m, 15)
    remit = _prior_month_payroll_liability(b, y, m)
    if remit.minor_units > 0:
        b.post(
            type=TxnType.EXPENSE, txn_date=remit_date, doc_number=f"CRA-{y}{m:02d}",
            memo="CRA source deduction remittance",
            lines=[
                b.line(C.PAYROLL_LIAB, remit),
                b.line(C.BANK, -remit),
            ],
        )

    # ---- credit card spend and payment ----------------------------------
    cc_total = Money.zero("CAD")
    for _ in range(rng.randint(5, 11)):
        acct = rng.choices(
            [C.VEHICLE, C.OFFICE, C.MEALS, C.REPAIRS, C.ADVERTISING],
            weights=[34, 24, 14, 18, 10],
        )[0]
        net = _d(f"{rng.randint(45, 940)}.{rng.randint(0, 99):02d}")
        hst = net.scale(HST_RATE)
        gross = net + hst
        cc_total = cc_total + gross
        b.post(
            type=TxnType.EXPENSE, txn_date=_clamp_day(y, m, rng.randint(1, 28)),
            memo="Card purchase",
            lines=[
                b.line(acct, net, tax_code="HST-ON", tax_amount=hst),
                b.line(C.HST, hst, memo="Input tax credit"),
                b.line(C.VISA, -gross),
            ],
        )
    if cc_total.minor_units > 0:
        b.post(
            type=TxnType.TRANSFER, txn_date=_clamp_day(y, m, 22),
            doc_number=f"CCPAY-{y}{m:02d}", memo="Visa payment",
            lines=[b.line(C.VISA, cc_total), b.line(C.BANK, -cc_total)],
        )

    # ---- loan payment: principal + interest ------------------------------
    outstanding = _loan_outstanding(b)
    interest = outstanding.scale(Decimal("0.079") / Decimal(12))
    payment = _d("4180.00")
    principal = payment - interest
    b.post(
        type=TxnType.EXPENSE, txn_date=_clamp_day(y, m, 5), doc_number=f"LOAN-{y}{m:02d}",
        memo="Equipment loan payment",
        lines=[
            b.line(C.LOAN, principal, memo="Principal portion"),
            b.line(C.INTEREST, interest, memo="Interest portion"),
            b.line(C.BANK, -payment),
        ],
    )

    # ---- monthly depreciation and prepaid amortization --------------------
    b.post(
        type=TxnType.JOURNAL_ENTRY, txn_date=m_end, doc_number=f"JE-DEP-{y}{m:02d}",
        memo="Monthly depreciation", is_manual=True, is_adjusting=True, created_by="close_process",
        lines=[b.line(C.DEPRECIATION, _d("7450.00")), b.line(C.ACCUM_DEP, _d("-7450.00"))],
    )
    b.post(
        type=TxnType.JOURNAL_ENTRY, txn_date=m_end, doc_number=f"JE-INS-{y}{m:02d}",
        memo="Insurance amortization", is_manual=True, is_adjusting=True, created_by="close_process",
        lines=[b.line(C.INSURANCE, _d("600.00")), b.line(C.PREPAID_INSURANCE, _d("-600.00"))],
    )
    # Annual insurance renewal each April keeps the prepaid account alive.
    if m == 4:
        b.post(
            type=TxnType.EXPENSE, txn_date=date(y, 4, 1), doc_number=f"INS-{y}",
            party_id="VEND-06", memo="Annual liability and fleet insurance renewal",
            lines=[b.line(C.PREPAID_INSURANCE, _d("7200.00")), b.line(C.BANK, _d("-7200.00"))],
        )

    # ---- rent, bank charges, owner draws ---------------------------------
    b.post(
        type=TxnType.EXPENSE, txn_date=date(y, m, 1), doc_number=f"RENT-{y}{m:02d}",
        party_id="VEND-07", memo="Yard and shop rent",
        lines=[
            b.line(C.RENT, _d("3800.00"), party_id="VEND-07", tax_code="HST-ON",
                   tax_amount=_d("494.00")),
            b.line(C.HST, _d("494.00"), memo="Input tax credit"),
            b.line(C.BANK, _d("-4294.00")),
        ],
    )
    b.post(
        type=TxnType.EXPENSE, txn_date=m_end, doc_number=f"BKCHG-{y}{m:02d}",
        memo="Bank service charges",
        lines=[b.line(C.BANK_CHARGES, _d("88.50")), b.line(C.BANK, _d("-88.50"))],
    )
    if rng.random() < 0.6:
        draw = _d(f"{rng.randint(4, 12) * 1000}.00")
        b.post(
            type=TxnType.EXPENSE, txn_date=_clamp_day(y, m, rng.randint(10, 26)),
            doc_number=f"DRAW-{y}{m:02d}", memo="Shareholder draw",
            lines=[b.line(C.OWNER_DRAWS, draw), b.line(C.BANK, -draw)],
        )

    # ---- quarterly HST remittance -----------------------------------------
    if m in (1, 4, 7, 10):
        owing = _hst_balance_before(b, date(y, m, 1))
        if owing.minor_units > 0:
            b.post(
                type=TxnType.EXPENSE, txn_date=_clamp_day(y, m, 28),
                doc_number=f"HST-{y}Q{(m - 1) // 3 + 1}", memo="HST remittance to CRA",
                lines=[b.line(C.HST, owing), b.line(C.BANK, -owing)],
            )

    return invoice_no, bill_no


def _vendor_name(vend_id: str) -> str:
    for pid, pname, _a, _t in VENDORS:
        if pid == vend_id:
            return pname
    return vend_id


def _prior_month_payroll_liability(b: LedgerBuilder, y: int, m: int) -> Money:
    prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
    start = date(prev_y, prev_m, 1)
    stop = _month_end(prev_y, prev_m)
    total = Money.zero("CAD")
    for txn in b.transactions:
        if txn.type is not TxnType.PAYROLL or not (start <= txn.txn_date <= stop):
            continue
        for ln in txn.lines:
            if ln.account_id == C.PAYROLL_LIAB:
                total = total - ln.amount  # credit balance -> positive remittance
    return total


def _loan_outstanding(b: LedgerBuilder) -> Money:
    total = Money.zero("CAD")
    for txn in b.transactions:
        for ln in txn.lines:
            if ln.account_id == C.LOAN:
                total = total - ln.amount
    return total


def _hst_balance_before(b: LedgerBuilder, before: date) -> Money:
    total = Money.zero("CAD")
    for txn in b.transactions:
        if txn.txn_date >= before:
            continue
        for ln in txn.lines:
            if ln.account_id == C.HST:
                total = total - ln.amount
    return total


def _build_statements(ledger: Ledger, start: date, end: date) -> list[BankStatement]:
    """Derive monthly bank statements from the ledger, with realistic timing gaps.

    Cheques written in the last three days of a month clear in the next one. That
    single detail is what makes reconciliation a real test instead of a lookup.
    """
    ledger.build_indexes()
    statements: list[BankStatement] = []
    for account_id, bank_id in ((C.BANK, "BANK-CHQ"), (C.VISA, "BANK-VISA")):
        postings = sorted(
            ledger.postings(account_id), key=lambda tl: (tl[0].txn_date, tl[1].line_id)
        )
        for y, m in _month_iter(start, end):
            p_start, p_end = date(y, m, 1), _month_end(y, m)
            opening = Money.zero("CAD")
            lines: list[StatementLine] = []
            movement = Money.zero("CAD")
            for txn, line in postings:
                clears = txn.txn_date
                # Payments issued at month end clear a few days later.
                if txn.type in (TxnType.BILL_PAYMENT, TxnType.TRANSFER) and (
                    p_end - txn.txn_date
                ).days < 3:
                    clears = txn.txn_date + timedelta(days=4)
                if clears < p_start:
                    opening = opening + line.amount
                elif clears <= p_end:
                    movement = movement + line.amount
                    lines.append(
                        StatementLine(
                            statement_line_id=f"ST-{bank_id}-{line.line_id}",
                            posted_on=clears,
                            amount=line.amount,
                            description=(txn.memo or txn.type.value)[:80],
                            reference=txn.doc_number,
                        )
                    )
            statements.append(
                BankStatement(
                    bank_account_id=bank_id, start=p_start, end=p_end,
                    opening_balance=opening, closing_balance=opening + movement,
                    lines=tuple(lines),
                )
            )
    return statements
