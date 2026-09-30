"""A synthetic development project ledger built from an engagement's facts.

Four months of a fourplex build: land brought in at cost, owner cash,
construction-loan draws, trade bills with recoverable HST and a 10%
holdback, soft costs, capitalised interest. Balanced, reconciled, and
deliberately clean, so the development controls prove quiet on a correct
book and loud on a mutated one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..canonical.enums import PartyType, TxnType
from ..canonical.models import (
    Application,
    BankAccount,
    Ledger,
    LegalEntity,
    Lineage,
    OpenItem,
    Party,
    Tenant,
)
from ..engine.reconcile import BankStatement, StatementLine
from ..money import Money
from ..realestate.chart import DEVELOPMENT_ACCOUNTS as A
from ..realestate.chart import build_development_chart
from ..realestate.facts import EngagementFacts
from .fixture_contractor import LedgerBuilder, _calendar, _month_end, _month_iter

__all__ = ["DevelopmentFixture", "build_development_project"]

SOURCE = "fixture_development"
HST = Decimal("0.13")
HOLDBACK = Decimal("0.10")

TRADES: tuple[tuple[str, str, str], ...] = (
    ("VEND-EXC", "Northgate Excavating Ltd", "hard"),
    ("VEND-FORM", "Bluestone Forming & Foundations Inc", "hard"),
    ("VEND-FRAME", "Maple Ridge Framing Co", "hard"),
    ("VEND-PLUMB", "Riverside Plumbing & Mechanical", "hard"),
    ("VEND-ELEC", "Lakeshore Electrical Ltd", "hard"),
    ("VEND-ARCH", "Studio Nine Architects", "soft"),
    ("VEND-ENG", "Quadrant Structural Engineering", "soft"),
    ("VEND-CITY", "City of Toronto (permits and development charges)", "soft"),
    ("VEND-INS", "Dominion Builders Risk Insurance", "soft"),
    ("VEND-LAW", "Harbourview Law LLP", "soft"),
    ("VEND-BANK", "Meridian Construction Lending", "lender"),
)


def _d(v: str) -> Money:
    return Money.from_decimal(v, "CAD")


@dataclass
class DevelopmentFixture:
    ledger: Ledger
    statements: list[BankStatement] = field(default_factory=list)
    period_start: date = date(2026, 6, 1)
    period_end: date = date(2026, 9, 30)
    fiscal_year_start: date = date(2026, 1, 1)
    facts: EngagementFacts | None = None


def build_development_project(facts: EngagementFacts, *, seed: int = 20260930, end: date | None = None,
                              tenant_id: str = "TEN-DEV", entity_id: str = "ENT-DEV") -> DevelopmentFixture:
    rng = random.Random(seed)
    start = facts.construction_start or date(2026, 6, 1)
    end = end or date(2026, 9, 30)
    name = facts.property_label or "Development project"
    tenant = Tenant(tenant_id=tenant_id, name=name, home_currency="CAD", jurisdiction="CA-ON")
    entity = LegalEntity(entity_id=entity_id, tenant_id=tenant_id, name=name, currency="CAD", jurisdiction="CA-ON")
    accounts = build_development_chart(entity_id)
    b = LedgerBuilder(entity_id)
    parties = {
        pid: Party(party_id=pid, entity_id=entity_id, name=pname, type=PartyType.VENDOR, payment_terms_days=30,
                   created_on=start - timedelta(days=rng.randint(30, 400)), lineage=Lineage(SOURCE, pid, f"vault://{SOURCE}/vendor/{pid}"))
        for pid, pname, _kind in TRADES
    }

    # Opening: land at cost, financed by the owners' equity; a little cash.
    land = (facts.purchase_price or _d("690000.00")) + facts.acquisition_costs
    opening = start - timedelta(days=1)
    b.post(type=TxnType.OPENING_BALANCE, txn_date=opening, created_by="migration", memo="Land brought into the project at cost",
           lines=[b.line(A["land"], land, memo="Land at cost"), b.line(A["bank"], _d("20000.00"), memo="Opening project cash"),
                  b.line(A["opening_equity"], -(land + _d("20000.00")), memo="Opening equity")])

    # Spend plan: the letter's $250,000 by late September, split over the months.
    target = facts.spent_to_date or _d("250000.00")
    months = list(_month_iter(start, end))
    weights = [1, 2, 3, 4][: len(months)] or [1]
    monthly_spend = target.allocate(weights + [4] * (len(months) - len(weights)))
    loan_balance = Money.zero("CAD")
    loan_limit = facts.facilities[0].limit if facts.facilities else _d("500000.00")
    rate = facts.facilities[0].annual_rate if facts.facilities else Decimal("0.085")
    bill_no = 1000
    for (y, m), spend in zip(months, monthly_spend, strict=False):
        m_start, m_end = date(y, m, 1), _month_end(y, m)
        # Owner contribution early each month, then a draw sized to the month's bills.
        owner_in = _d("15000.00") if m_start == start.replace(day=1) else _d("5000.00")
        b.post(type=TxnType.DEPOSIT, txn_date=m_start + timedelta(days=1), memo="Owner contribution, co-owner A", created_by="sync",
               lines=[b.line(A["bank"], owner_in), b.line(A["owner_a"], -owner_in)])
        # Bills: 80% hard, 20% soft, HST-inclusive amounts from the spend plan.
        hard_gross, soft_gross = spend.allocate([4, 1])
        for kind, gross in (("hard", hard_gross), ("soft", soft_gross)):
            vendors = [t for t in TRADES if t[2] == kind]
            parts = gross.allocate([1] * min(2, len(vendors)))
            for (pid, _pname, _k), part in zip(rng.sample(vendors, len(parts)), parts, strict=False):
                net = part.scale(Decimal(1) / (1 + HST))
                tax = part - net
                bill_no += 1
                cost_acct = A["hard_costs"] if kind == "hard" else A["soft_costs"]
                bill_date = m_start + timedelta(days=rng.randint(3, 20))
                bill = b.post(type=TxnType.BILL, txn_date=bill_date, party_id=pid, doc_number=f"INV-{bill_no}", created_by="sync",
                              memo=f"{parties[pid].name} progress billing", document_refs=(f"doc://{pid}/INV-{bill_no}",),
                              lines=[b.line(cost_acct, net, memo="Construction cost", party_id=pid, tax_code="HST", tax_amount=tax),
                                     b.line(A["hst_recoverable"], tax, memo="HST on construction input"),
                                     b.line(A["ap"], -part, party_id=pid)])
                item = OpenItem(open_item_id=f"OI-{bill.doc_number}", entity_id=entity_id, txn_id=bill.txn_id, party_id=pid, kind="payable",
                                issued_on=bill_date, due_on=bill_date + timedelta(days=30), original_amount=part, doc_number=bill.doc_number,
                                lineage=Lineage(SOURCE, bill.doc_number, f"vault://{SOURCE}/bill/{bill.doc_number}"))
                b.open_items.append(item)
                # Pay it, retaining the statutory holdback on trades.
                holdback = net.scale(HOLDBACK) if kind == "hard" else Money.zero("CAD")
                pay = part - holdback
                lines = [b.line(A["ap"], part, party_id=pid), b.line(A["bank"], -pay, memo=f"Payment {bill.doc_number}")]
                if holdback.minor_units:
                    lines.append(b.line(A["holdback_payable"], -holdback, memo="10% statutory holdback", party_id=pid))
                paid_on = min(bill_date + timedelta(days=rng.randint(10, 25)), m_end)
                payment = b.post(type=TxnType.BILL_PAYMENT, txn_date=paid_on, party_id=pid,
                                 doc_number=f"PAY-{bill_no}", memo=f"Payment to {parties[pid].name}", created_by="sync", lines=lines)
                b.applications.append(Application(application_id=f"APP-{bill.doc_number}", entity_id=entity_id, open_item_id=item.open_item_id,
                                                  payment_txn_id=payment.txn_id, applied_on=paid_on, amount=part,
                                                  lineage=Lineage(SOURCE, f"app-{bill.doc_number}")))
        # Draw to cover the month, up to the limit.
        need = spend - owner_in
        draw = max(min(need, loan_limit - loan_balance), Money.zero("CAD"))
        if draw.minor_units:
            b.post(type=TxnType.DEPOSIT, txn_date=m_start + timedelta(days=8), party_id="VEND-BANK", memo="Construction loan draw", created_by="sync",
                   doc_number=f"DRAW-{y}{m:02d}", document_refs=(f"doc://lender/draw-{y}{m:02d}",),
                   lines=[b.line(A["bank"], draw), b.line(A["construction_loan"], -draw)])
            loan_balance = loan_balance + draw
        interest = loan_balance.scale(rate / Decimal(12))
        if interest.minor_units:
            b.post(type=TxnType.EXPENSE, txn_date=m_end, party_id="VEND-BANK", memo="Construction loan interest, capitalised", created_by="sync",
                   doc_number=f"INT-{y}{m:02d}", document_refs=(f"doc://lender/stmt-{y}{m:02d}",),
                   lines=[b.line(A["capitalised_interest"], interest), b.line(A["bank"], -interest)])

    ledger = Ledger(
        tenant=tenant, entity=entity, calendar=_calendar(entity_id, start, end), accounts=accounts, parties=parties, jobs={},
        bank_accounts={"BANK-PROJ": BankAccount(bank_account_id="BANK-PROJ", entity_id=entity_id, account_id=A["bank"],
                                                 name="Project account", institution="Meridian Credit Union", masked_number="****4421",
                                                 lineage=Lineage(SOURCE, "BANK-PROJ", f"vault://{SOURCE}/bank/BANK-PROJ"))},
        transactions=b.transactions, open_items=b.open_items, applications=b.applications,
    ).build_indexes()
    statements = _statements(ledger, A["bank"], start, end)
    return DevelopmentFixture(ledger=ledger, statements=statements, period_start=start, period_end=end,
                              fiscal_year_start=date(start.year, 1, 1), facts=facts)


def _statements(ledger: Ledger, account_id: str, start: date, end: date) -> list[BankStatement]:
    postings = sorted(ledger.postings(account_id), key=lambda tl: (tl[0].txn_date, tl[1].line_id))
    out: list[BankStatement] = []
    for y, m in _month_iter(start, end):
        p_start, p_end = date(y, m, 1), _month_end(y, m)
        opening = Money.zero("CAD")
        movement = Money.zero("CAD")
        lines: list[StatementLine] = []
        for txn, line in postings:
            if txn.txn_date < p_start:
                opening = opening + line.amount
            elif txn.txn_date <= p_end:
                movement = movement + line.amount
                lines.append(StatementLine(statement_line_id=f"ST-{line.line_id}", posted_on=txn.txn_date, amount=line.amount,
                                           description=(txn.memo or txn.type.value)[:80], reference=txn.doc_number))
        out.append(BankStatement(bank_account_id="BANK-PROJ", start=p_start, end=p_end, opening_balance=opening,
                                 closing_balance=opening + movement, lines=tuple(lines)))
    return out
