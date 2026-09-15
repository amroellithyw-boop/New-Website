"""Control 56: job margin far below the typical job.

A job losing money, or earning a fraction of what the rest of the book earns,
is either underpriced, over-run, or has costs coded to it that belong
elsewhere. Any of the three is worth a conversation before the next job like
it is quoted.

The reference is the median job margin, not the company gross margin: costs
that were never allocated to a job, or errors elsewhere in the book, move the
company margin without saying anything about any job. The company margin is
still recorded in the packet for context.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from statistics import median

from ...canonical.enums import RiskTier, Severity
from ...cfo.jobs import job_profitability
from ..base import Finding, Rule, RuleContext, register


@register
class JobMarginOutlier(Rule):
    rule_id = "FOS-R056"
    version = "1"
    title = "Job margin well below the company average"
    purpose = "Find jobs that are losing money or earning far less than the rest of the book."
    category = "profitability"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = ("Confirm all costs on the job belong to it, compare the quote to actual hours and materials, "
                   "and reprice the next job of this type.")
    evidence_required = ("job revenue", "job direct costs", "company gross margin")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        jobs = job_profitability(ctx)
        if len(jobs) < 3:
            return
        company_margin = ctx.pl_ttm.gross_margin
        margins = [j.margin for j in jobs if j.margin is not None and j.revenue >= ctx.materiality.performance]
        if len(margins) < 3:
            return
        typical = median(margins)
        if typical <= 0:
            return
        for j in jobs:
            if j.revenue < ctx.materiality.performance or j.margin is None:
                continue
            gap = typical - j.margin
            if j.margin >= 0 and gap < typical * Decimal("0.5"):
                continue
            shortfall = j.revenue.scale(gap)
            if shortfall < ctx.materiality.performance:
                continue
            builder = ctx.packet(f"{self.rule_id}-{j.job_id}", f"Compare job {j.name} against the company margin")
            for txn in [t for t in ctx.ledger.transactions if (t.job_id == j.job_id or any(ln.job_id == j.job_id for ln in t.lines))][:10]:
                builder.transaction(txn, label=f"Posting on job {j.name}")
            builder.calc("job revenue", "sum(revenue lines on the job, trailing twelve months)", j.revenue)
            builder.calc("job direct cost", "sum(cost of sales lines on the job)", j.direct_cost)
            builder.calc("job margin", "gross profit / revenue", j.margin.quantize(Decimal("0.001")))
            builder.calc("typical job margin", "median of job margins with material revenue", typical.quantize(Decimal("0.001")))
            if company_margin is not None:
                builder.calc("company gross margin", "trailing twelve months, for context", company_margin.quantize(Decimal("0.001")))
            builder.calc("profit shortfall against the typical job", "(typical margin - job margin) x job revenue", shortfall)
            builder.context(customer=j.customer, job=j.job_id)
            yield self.finding(ctx, suffix=j.job_id,
                               title=f"{j.name} earns {j.margin * 100:.1f}% against a typical job margin of {typical * 100:.1f}%",
                               narrative=(f"{j.name} for {j.customer} produced {j.gross_profit.format()} on {j.revenue.format()} of revenue. "
                                          f"At the typical job's margin it would have produced {shortfall.format()} more."),
                               exposure=shortfall, packet=builder.build(), confidence=Decimal("0.75"))
