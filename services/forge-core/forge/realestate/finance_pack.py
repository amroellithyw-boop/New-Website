"""The monthly Owner Finance Pack, from the project ledger.

Cost to date by category against budget, draws against facilities,
capitalised interest, recoverable HST, missing support, projected cash
requirement, and the items that need the owner's attention. Every figure is
a ledger total or a control finding; nothing is typed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..canonical.enums import AccountSubtype
from ..money import Money
from ..rules.base import Finding, RuleContext
from .chart import DEVELOPMENT_ACCOUNTS
from .facts import EngagementFacts
from .project import CashMonth, ProjectStatus, cash_requirement_schedule, project_status

__all__ = ["FinancePack", "owner_finance_pack", "render_finance_pack"]


@dataclass(frozen=True)
class CategoryLine:
    label: str
    account_id: str
    period: Money
    to_date: Money
    budget: Money | None
    percent_of_budget: Decimal | None


@dataclass
class FinancePack:
    project: str
    period_start: date
    period_end: date
    categories: list[CategoryLine]
    cost_to_date: Money
    hst_recoverable_balance: Money
    hst_recoverable_period: Money
    holdbacks_payable: Money
    facilities: list[tuple[str, Money, Money]]  # name, drawn, limit
    capitalised_interest_period: Money
    capitalised_interest_to_date: Money
    owner_contributions_to_date: Money
    bank_balance: Money
    bank_reconciled: bool | None
    missing_support: list[tuple[str, date, Money, str]]
    status: ProjectStatus
    cash_schedule: list[CashMonth]
    attention: list[Finding] = field(default_factory=list)


def _closing(ctx: RuleContext, account_id: str) -> Money:
    for row in ctx.tb.rows:
        if row.account.account_id == account_id:
            return row.closing
    return ctx.zero()


def _movement(ctx: RuleContext, account_id: str) -> Money:
    for row in ctx.tb.rows:
        if row.account.account_id == account_id:
            return row.movement
    return ctx.zero()


def owner_finance_pack(ctx: RuleContext, facts: EngagementFacts, findings: list[Finding] | None = None,
                       *, budget_by_category: dict[str, Money] | None = None) -> FinancePack:
    acc = {**DEVELOPMENT_ACCOUNTS, **ctx.policy.get("development_accounts", {})}
    cur = ctx.currency
    budgets = budget_by_category or {}
    cats: list[CategoryLine] = []
    cost_to_date = Money.zero(cur)
    for label, key in (("Land", "land"), ("Hard costs", "hard_costs"), ("Soft costs", "soft_costs"),
                       ("Capitalised interest", "capitalised_interest"), ("Garden suite", "garden_suite")):
        aid = acc[key]
        period, to_date = _movement(ctx, aid), _closing(ctx, aid)
        budget = budgets.get(key)
        pct = (Decimal(to_date.minor_units) / Decimal(budget.minor_units)) if budget and budget.minor_units else None
        cats.append(CategoryLine(label, aid, period, to_date, budget, pct))
        if key != "land":
            cost_to_date = cost_to_date + to_date
    facilities: list[tuple[str, Money, Money]] = []
    for i, key in enumerate(("construction_loan", "construction_facility")):
        aid = acc[key]
        a = ctx.ledger.account(aid)
        if a is None:
            continue
        drawn = -_closing(ctx, aid)
        limit = facts.facilities[i].limit if i < len(facts.facilities) else Money.zero(cur)
        label = facts.facilities[i].name if i < len(facts.facilities) else a.name
        facilities.append((label, drawn, limit))
    owner = Money.zero(cur)
    for a in ctx.ledger.accounts.values():
        if a.subtype is AccountSubtype.OWNER_CONTRIBUTION:
            owner = owner - _closing(ctx, a.account_id)
    bank_id = acc["bank"]
    rec = next((r for k, r in ctx.reconciliations.items() if ctx.ledger.bank_accounts.get(k) and ctx.ledger.bank_accounts[k].account_id == bank_id), None)
    missing: list[tuple[str, date, Money, str]] = []
    threshold = Money.from_decimal("500.00", cur)
    for t in ctx.ledger.transactions:
        if ctx.period_start <= t.txn_date <= ctx.period_end and t.type.value in ("bill", "expense", "cheque") and not t.document_refs:
            amt = Money.zero(cur)
            for ln in t.lines:
                if ln.amount.minor_units > 0:
                    amt = amt + ln.amount
            if amt >= threshold:
                party = ctx.ledger.parties.get(t.party_id) if t.party_id else None
                missing.append((t.txn_id, t.txn_date, amt, party.name if party else (t.memo or "")))
    status = project_status(facts, today=ctx.period_end)
    return FinancePack(
        project=facts.property_label or ctx.ledger.entity.name, period_start=ctx.period_start, period_end=ctx.period_end,
        categories=cats, cost_to_date=cost_to_date,
        hst_recoverable_balance=_closing(ctx, acc["hst_recoverable"]), hst_recoverable_period=_movement(ctx, acc["hst_recoverable"]),
        holdbacks_payable=-_closing(ctx, acc["holdback_payable"]), facilities=facilities,
        capitalised_interest_period=_movement(ctx, acc["capitalised_interest"]), capitalised_interest_to_date=_closing(ctx, acc["capitalised_interest"]),
        owner_contributions_to_date=owner, bank_balance=_closing(ctx, bank_id),
        bank_reconciled=(rec.is_reconciled if rec else None), missing_support=missing, status=status,
        cash_schedule=cash_requirement_schedule(facts, today=ctx.period_end), attention=list(findings or []),
    )


def render_finance_pack(p: FinancePack) -> str:
    lines = [f"Owner Finance Pack: {p.project}", f"Period {p.period_start} to {p.period_end}", ""]
    lines.append("Cost to date by category")
    for c in p.categories:
        b = f"  budget {c.budget.format():>14s}  {c.percent_of_budget:.0%}" if c.budget and c.percent_of_budget is not None else ""
        lines.append(f"  {c.label:<24s} this period {c.period.format():>14s}   to date {c.to_date.format():>14s}{b}")
    lines.append(f"  {'Building cost to date':<24s} {'':>26s}   {p.cost_to_date.format():>22s}")
    lines.append("")
    lines.append("Financing")
    for name, drawn, limit in p.facilities:
        room = limit - drawn if limit.minor_units else None
        lines.append(f"  {name:<34s} drawn {drawn.format():>14s} of {limit.format():>14s}" + (f"   room {room.format()}" if room is not None else ""))
    lines.append(f"  {'Owner contributions to date':<34s} {p.owner_contributions_to_date.format():>20s}")
    lines.append(f"  {'Interest capitalised this period':<34s} {p.capitalised_interest_period.format():>20s}   to date {p.capitalised_interest_to_date.format()}")
    lines.append("")
    lines.append("GST/HST and holdbacks")
    lines.append(f"  {'HST recoverable, this period':<34s} {p.hst_recoverable_period.format():>20s}")
    lines.append(f"  {'HST recoverable, balance to claim':<34s} {p.hst_recoverable_balance.format():>20s}")
    lines.append(f"  {'Holdbacks payable':<34s} {p.holdbacks_payable.format():>20s}")
    lines.append("")
    st = p.status
    lines.append("Budget and cash")
    lines.append(f"  Budget {st.budget.format()}, spent {st.spent.format()} ({st.percent_spent:.0%}), cost to complete {st.cost_to_complete.format()} over {st.months_remaining} months")
    lines.append(f"  Facilities {st.facility_limit.format()}, undrawn {st.undrawn.format()}; owner equity still required {st.owner_equity_required.format()}")
    if st.loan_to_value is not None:
        lines.append(f"  Loan to cost {st.loan_to_cost:.0%}, loan to value {st.loan_to_value:.0%}, equity created at completion {st.equity_created.format()}")
    lines.append("  Next six months: spend / draw / owner")
    for m in p.cash_schedule[:6]:
        lines.append(f"    {m.month.strftime('%b %Y')}  {m.spend.format():>12s} {m.draw.format():>12s} {m.owner_contribution.format():>12s}")
    lines.append("")
    lines.append(f"Bank {p.bank_balance.format()}, " + ("reconciled" if p.bank_reconciled else "NOT reconciled" if p.bank_reconciled is False else "no statement supplied"))
    if p.missing_support:
        lines.append(f"Missing support ({len(p.missing_support)}): invoices over $500 with no document attached")
        for tid, d, amt, who in p.missing_support[:10]:
            lines.append(f"  {d}  {amt.format():>12s}  {who}  ({tid})")
    if p.attention:
        lines.append(f"Items needing attention ({len(p.attention)})")
        for f in p.attention[:10]:
            lines.append(f"  [{f.severity.value}] {f.rule_id} {f.title}")
    return "\n".join(lines)
