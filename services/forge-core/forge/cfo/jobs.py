"""Job profitability: the analysis a contractor actually runs the business on.

Every canonical line carries a job id, so revenue and direct cost by job come
straight from the ledger. Margin by job against the company's own average and
the industry band is where underpricing and cost overruns become visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..canonical.enums import AccountType
from ..engine.statements import COGS_SUBTYPES
from ..money import Money
from ..rules.base import RuleContext

__all__ = ["JobResult", "job_profitability"]


@dataclass(frozen=True)
class JobResult:
    job_id: str
    name: str
    customer: str
    revenue: Money
    direct_cost: Money
    contract_value: Money | None

    @property
    def gross_profit(self) -> Money:
        return self.revenue - self.direct_cost

    @property
    def margin(self) -> Decimal | None:
        return self.gross_profit.ratio_to(self.revenue)

    @property
    def billed_percent_of_contract(self) -> Decimal | None:
        return self.revenue.ratio_to(self.contract_value) if self.contract_value else None

    def to_dict(self) -> dict:
        return {"job": self.job_id, "name": self.name, "customer": self.customer, "revenue": str(self.revenue.to_decimal()),
                "direct_cost": str(self.direct_cost.to_decimal()), "gross_profit": str(self.gross_profit.to_decimal()),
                "margin": f"{self.margin:.3f}" if self.margin is not None else None}


def job_profitability(ctx: RuleContext, *, trailing_twelve_months: bool = True) -> list[JobResult]:
    cur = ctx.currency
    start = ctx.period_end.replace(year=ctx.period_end.year - 1) if trailing_twelve_months else ctx.period_start
    rev: dict[str, Money] = {}
    cost: dict[str, Money] = {}
    for txn in ctx.ledger.transactions:
        if not (start < txn.txn_date <= ctx.period_end):
            continue
        for line in txn.lines:
            job = line.job_id or txn.job_id
            if not job:
                continue
            acct = ctx.ledger.account(line.account_id)
            if acct is None:
                continue
            if acct.type is AccountType.REVENUE:
                rev[job] = rev.get(job, Money.zero(cur)) - line.amount
            elif acct.subtype in COGS_SUBTYPES:
                cost[job] = cost.get(job, Money.zero(cur)) + line.amount
    out = []
    for job_id in set(rev) | set(cost):
        j = ctx.ledger.jobs.get(job_id)
        customer = ctx.ledger.parties.get(j.customer_id).name if j and j.customer_id and j.customer_id in ctx.ledger.parties else (j.customer_id if j else "")
        out.append(JobResult(job_id, j.name if j else job_id, customer or "", rev.get(job_id, Money.zero(cur)), cost.get(job_id, Money.zero(cur)), j.contract_value if j else None))
    return sorted(out, key=lambda r: (r.margin if r.margin is not None else Decimal(99)))
