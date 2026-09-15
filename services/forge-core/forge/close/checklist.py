"""The month-end close as a checklist whose status the ledger proves.

A close checklist that a person ticks is a list of hopes. This one derives each
task's status from the ledger and the control results wherever that is
possible: depreciation is done when it is posted, a reconciliation is done when
it reconciles, an accrual review is done when the accrual control is silent.
Only tasks that genuinely need a human judgement stay open for one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from ..canonical.enums import AccountSubtype, AgentRole
from ..clients.profile import ClientProfile
from ..money import msum
from ..rules.base import Finding, RuleContext

__all__ = ["CloseTask", "CloseChecklist", "build_close_checklist"]

Status = Literal["done", "open", "blocked", "not_applicable"]


@dataclass(frozen=True)
class CloseTask:
    task_id: str
    title: str
    owner: AgentRole
    status: Status
    evidence: str
    related_rules: tuple[str, ...] = ()
    order: int = 0


@dataclass
class CloseChecklist:
    period_start: date
    period_end: date
    tasks: list[CloseTask] = field(default_factory=list)

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.status == "done")

    @property
    def open(self) -> list[CloseTask]:
        return [t for t in self.tasks if t.status in ("open", "blocked")]

    @property
    def applicable(self) -> int:
        return sum(1 for t in self.tasks if t.status != "not_applicable")

    @property
    def percent_complete(self) -> int:
        return int(100 * self.done / self.applicable) if self.applicable else 100

    @property
    def can_lock(self) -> bool:
        return not any(t.status == "blocked" for t in self.tasks) and all(
            t.status in ("done", "not_applicable") for t in self.tasks if t.task_id != "lock_period")

    def to_dict(self) -> dict:
        return {"period": f"{self.period_start}..{self.period_end}", "percent_complete": self.percent_complete,
                "can_lock": self.can_lock,
                "tasks": [{"id": t.task_id, "title": t.title, "owner": t.owner.value, "status": t.status,
                           "evidence": t.evidence, "rules": list(t.related_rules)} for t in self.tasks]}


def build_close_checklist(ctx: RuleContext, findings: Iterable[Finding], profile: ClientProfile | None = None) -> CloseChecklist:
    fired = {f.rule_id for f in findings}
    cur = ctx.currency
    tasks: list[CloseTask] = []
    n = 0

    def add(task_id, title, owner, status, evidence, rules=()):
        nonlocal n
        n += 1
        tasks.append(CloseTask(task_id, title, owner, status, evidence, tuple(rules), n))

    def rule_status(rules: tuple[str, ...], done_text: str, open_text: str, blocked: bool = False) -> tuple[Status, str]:
        hit = [r for r in rules if r in fired]
        if hit:
            return ("blocked" if blocked else "open"), f"{open_text}: {', '.join(hit)}"
        return "done", done_text

    # 1. Integrity first.
    ties = ctx.tb.closing_imbalance.is_zero and ctx.bs.equation_difference.is_zero and not ctx.tb.unbalanced_transactions
    add("tie_out", "Trial balance and balance sheet tie out", AgentRole.CLOSE_ACCOUNTANT,
        "done" if ties else "blocked", "Books tie to zero" if ties else "Books do not tie; nothing else can be signed", ("FOS-R001", "FOS-R004"))

    # 2. Reconciliations, one per bank or card account.
    for bank_id, rec in ctx.reconciliations.items():
        bank = ctx.ledger.bank_accounts.get(bank_id)
        name = bank.name if bank else bank_id
        add(f"reconcile_{bank_id}", f"Reconcile {name}", AgentRole.BOOKKEEPER,
            "done" if rec.is_reconciled else "blocked",
            "Fully explained" if rec.is_reconciled else f"Unexplained {rec.unexplained_difference.format()}, {len(rec.bank_only_items)} bank-only items",
            ("FOS-R008", "FOS-R009", "FOS-R010"))
    if not ctx.reconciliations:
        add("reconcile_all", "Reconcile every bank and card account", AgentRole.BOOKKEEPER, "open", "No statements were supplied for this period", ("FOS-R008",))

    # 3. Subledgers.
    s, e = rule_status(("FOS-R027", "FOS-R029", "FOS-R031"), "Aging reviewed; no exceptions above threshold", "Receivable exceptions to work")
    add("ar_review", f"Review receivables aging ({ctx.ar.total.format()}, {ctx.ar.past_due_total().format()} past due)", AgentRole.AR_SPECIALIST, s, e, ("FOS-R026", "FOS-R027", "FOS-R028", "FOS-R029", "FOS-R031"))
    s, e = rule_status(("FOS-R006", "FOS-R030", "FOS-R032"), "Payables reviewed; no exceptions", "Payable exceptions to work", blocked="FOS-R032" in fired)
    add("ap_review", f"Review payables aging ({ctx.ap.total.format()})", AgentRole.AP_SPECIALIST, s, e, ("FOS-R006", "FOS-R030", "FOS-R032", "FOS-R033"))

    # 4. Recurring adjustments, proven from postings.
    fixed = msum((r.presentation_balance for r in ctx.tb.rows_of(subtype=AccountSubtype.FIXED_ASSET)), cur)
    dep = msum((r.presentation_movement for r in ctx.tb.rows_of(subtype=AccountSubtype.DEPRECIATION_EXPENSE)), cur)
    if fixed.minor_units > 0:
        add("depreciation", "Post monthly depreciation", AgentRole.CLOSE_ACCOUNTANT,
            "done" if dep.minor_units > 0 and "FOS-R042" not in fired else "open",
            f"{dep.format()} posted against {fixed.format()} of assets", ("FOS-R042",))
    else:
        add("depreciation", "Post monthly depreciation", AgentRole.CLOSE_ACCOUNTANT, "not_applicable", "No fixed assets")

    prepaid_rows = ctx.tb.rows_of(subtype=AccountSubtype.PREPAID_EXPENSE)
    prepaid_bal = msum((r.presentation_balance for r in prepaid_rows), cur)
    prepaid_rel = msum((r.credits for r in prepaid_rows), cur)
    if prepaid_bal.minor_units > 0:
        add("prepaids", "Amortise prepaid expenses", AgentRole.CLOSE_ACCOUNTANT,
            "done" if prepaid_rel.minor_units > 0 and "FOS-R043" not in fired else "open",
            f"{prepaid_rel.format()} released; {prepaid_bal.format()} remains", ("FOS-R043",))
    else:
        add("prepaids", "Amortise prepaid expenses", AgentRole.CLOSE_ACCOUNTANT, "not_applicable", "No prepaid balance")

    expects_deferred = bool(profile and profile.needs_deferred_revenue_control) or bool(ctx.policy.get("deferred_revenue_expected"))
    def_rows = ctx.tb.rows_of(subtype=AccountSubtype.DEFERRED_REVENUE)
    def_bal = msum((r.presentation_balance for r in def_rows), cur)
    def_rel = msum((r.debits for r in def_rows), cur)
    if expects_deferred or def_bal.minor_units > 0:
        add("deferred_revenue", "Recognise deferred revenue for the month", AgentRole.CLOSE_ACCOUNTANT,
            "done" if def_rel.minor_units > 0 else "open",
            f"{def_rel.format()} recognised; {def_bal.format()} deferred" + ("" if def_bal.minor_units > 0 else "; no deferred balance carried for a business that sells prepaid contracts"),
            ("FOS-R053",))
    else:
        add("deferred_revenue", "Recognise deferred revenue", AgentRole.CLOSE_ACCOUNTANT, "not_applicable", "Industry does not defer revenue")

    s, e = rule_status(("FOS-R039", "FOS-R044"), "Accruals reviewed; reversals complete", "Accrual exceptions")
    add("accruals", "Review accruals and reversals", AgentRole.CLOSE_ACCOUNTANT, s, e, ("FOS-R039", "FOS-R044"))
    s, e = rule_status(("FOS-R045", "FOS-R046", "FOS-R047"), "Loan balances and interest agree to schedule", "Debt exceptions")
    add("debt", "Reconcile loans to lender statements", AgentRole.TREASURY_SPECIALIST, s, e if ctx.ledger.debts else "No debt instruments", ("FOS-R045", "FOS-R046", "FOS-R047"))

    # 5. Payroll and tax.
    has_payroll = any(t.type.value == "payroll" for t in ctx.ledger.transactions_in(ctx.ledger.calendar.period_for(ctx.period_end)) if ctx.ledger.calendar.period_for(ctx.period_end)) if ctx.ledger.calendar.periods else False
    s, e = rule_status(("FOS-R037", "FOS-R038"), "Payroll ties and remittances are current", "Payroll exceptions", blocked="FOS-R038" in fired)
    add("payroll", "Tie payroll to the ledger and confirm remittances", AgentRole.PAYROLL_SPECIALIST, s if has_payroll or "payroll" in (profile.services if profile else ()) else "not_applicable", e, ("FOS-R037", "FOS-R038", "FOS-R039"))
    s, e = rule_status(("FOS-R035", "FOS-R036"), "Sales tax account reconciles to activity", "Sales tax exceptions")
    add("sales_tax", "Reconcile the sales tax account", AgentRole.TAX_SPECIALIST, s, e, ("FOS-R035", "FOS-R036"))

    # 6. Period control and sign-off.
    s, e = rule_status(("FOS-R011", "FOS-R012", "FOS-R013", "FOS-R014"), "No closed-period or period-end anomalies", "Period control exceptions", blocked="FOS-R011" in fired)
    add("period_control", "Review period-end and closed-period entries", AgentRole.CONTROLLER, s, e, ("FOS-R011", "FOS-R012", "FOS-R013", "FOS-R014"))
    add("variance_review", "Explain material variances against prior period and prior year", AgentRole.FPA_ANALYST,
        "open" if any(r in fired for r in ("FOS-R020", "FOS-R021")) else "done", "Variances above materiality need a driver explanation" if any(r in fired for r in ("FOS-R020", "FOS-R021")) else "No unexplained variances", ("FOS-R020", "FOS-R021"))
    add("management_report", "Issue the management report", AgentRole.CONTROLLER, "open", "Produced by the review command once the items above are clear")
    period = ctx.ledger.calendar.period_for(ctx.period_end)
    add("lock_period", "Lock the period", AgentRole.CONTROLLER, "done" if period and period.is_closed else "open",
        "Locked" if period and period.is_closed else "Lock in the source system after sign-off")

    return CloseChecklist(ctx.period_start, ctx.period_end, tasks)
