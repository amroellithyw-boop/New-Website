"""Controls 57 to 62: construction accounting on a development project.

What a lender, an appraiser and the CRA each read in a project ledger, and
what goes wrong in one kept by a general bookkeeper: interest expensed during
the build, HST buried in cost, holdbacks not retained, draws that exceed the
facility, costs landing on land after the ground is broken, and soft costs
expensed instead of capitalised.

Account roles come from ``policy["development_accounts"]`` and default to the
ForgeOS development chart. A ledger without those accounts is simply out of
scope for these controls; they return nothing rather than guessing.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from ...canonical.enums import AccountSubtype, RiskTier, Severity, TxnType
from ...money import Money
from ...realestate.chart import DEVELOPMENT_ACCOUNTS
from ..base import Finding, Rule, RuleContext, register

SOFT_COST_WORDS = ("architect", "engineer", "permit", "development charge", "survey", "appraisal", "builder", "legal", "law ", "insurance")


def _accounts(ctx: RuleContext) -> dict[str, str]:
    return {**DEVELOPMENT_ACCOUNTS, **ctx.policy.get("development_accounts", {})}


def _in_scope(ctx: RuleContext) -> bool:
    acc = _accounts(ctx)
    return ctx.ledger.account(acc["hard_costs"]) is not None and ctx.ledger.account(acc["construction_loan"]) is not None


def _completion(ctx: RuleContext) -> date | None:
    raw = ctx.policy.get("project_completion_date")
    return date.fromisoformat(raw) if isinstance(raw, str) else raw


def _under_construction(ctx: RuleContext) -> bool:
    done = _completion(ctx)
    return done is None or done > ctx.period_end


@register
class ConstructionInterestExpensed(Rule):
    rule_id = "FOS-R057"
    version = "1"
    title = "Construction-period interest expensed rather than capitalised"
    purpose = "Interest and financing costs during construction form part of the building's cost (ITA s.18(3.1)); expensing them overstates the loss and understates the asset."
    category = "development"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Reclassify the interest to capitalised interest under building under construction; agree the total to the lender statements."
    evidence_required = ("interest postings", "project completion date")
    references = ("ITA s.18(3.1)",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not _in_scope(ctx) or not _under_construction(ctx):
            return
        for acct in ctx.ledger.accounts.values():
            if acct.subtype is not AccountSubtype.INTEREST_EXPENSE:
                continue
            rows = [(t, ln) for t, ln in ctx.ledger.postings(acct.account_id) if ctx.period_start <= t.txn_date <= ctx.period_end and ln.amount.minor_units > 0]
            if not rows:
                continue
            total = Money.zero(ctx.currency)
            for _t, ln in rows:
                total = total + ln.amount
            if total < ctx.materiality.trivial:
                continue
            builder = ctx.packet(f"{self.rule_id}-{acct.account_id}", "Interest expensed while the building is under construction")
            for t, _ln in rows[:10]:
                builder.transaction(t, label="Interest posted to expense")
            builder.calc("interest expensed in period", "sum(debits to interest expense)", total)
            builder.context(account=acct.account_id, completion=str(_completion(ctx)))
            yield self.finding(ctx, suffix=acct.account_id, title=f"{total.format()} of construction interest expensed instead of capitalised",
                               narrative=f"The project is under construction through {ctx.period_end}; {total.format()} of interest was posted to {acct.name}. "
                                         "Interest during construction is added to the cost of the building, not deducted.",
                               exposure=total, packet=builder.build())


@register
class ConstructionHstBuriedInCost(Rule):
    rule_id = "FOS-R058"
    version = "1"
    title = "HST on construction inputs left in cost rather than recorded as recoverable"
    purpose = "A registered builder recovers HST on construction inputs as input tax credits; HST left in the asset cost is cash left with the CRA and a wrong capital cost."
    category = "development"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Split each construction bill into net cost and HST recoverable; claim the credits on the next return."
    evidence_required = ("construction bills", "HST recoverable postings", "registration status")
    references = ("ETA s.169 input tax credits",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not _in_scope(ctx) or not ctx.policy.get("gst_registered", True):
            return
        acc = _accounts(ctx)
        cost_accounts = {acc["hard_costs"], acc["soft_costs"], acc.get("garden_suite", "")}
        suspects = []
        for t in ctx.ledger.transactions:
            if not (ctx.period_start <= t.txn_date <= ctx.period_end) or t.type is not TxnType.BILL:
                continue
            cost_lines = [ln for ln in t.lines if ln.account_id in cost_accounts and ln.amount.minor_units > 0]
            if not cost_lines:
                continue
            has_hst_line = any(ln.account_id == acc["hst_recoverable"] and ln.amount.minor_units > 0 for ln in t.lines)
            if not has_hst_line:
                suspects.append((t, cost_lines))
        if not suspects:
            return
        total = Money.zero(ctx.currency)
        for _t, lines in suspects:
            for ln in lines:
                total = total + ln.amount
        implied = total.scale(Decimal("13") / Decimal("113"))
        if implied < ctx.materiality.trivial:
            return
        builder = ctx.packet(f"{self.rule_id}-period", "Construction bills posted without an HST recoverable line")
        for t, _lines in suspects[:10]:
            builder.transaction(t, label="Bill with no HST recoverable line")
        builder.calc("cost posted without HST split", "sum(cost lines on bills lacking an HST line)", total)
        builder.calc("HST likely buried in cost", "cost x 13/113", implied)
        yield self.finding(ctx, suffix=ctx.period_end.strftime("%Y%m"),
                           title=f"About {implied.format()} of HST appears buried in construction cost on {len(suspects)} bill(s)",
                           narrative=f"{len(suspects)} construction bill(s) totalling {total.format()} carry no HST recoverable line. If the amounts are tax-inclusive, "
                                     f"about {implied.format()} of input tax credits are unclaimed and the capital cost is overstated by the same amount.",
                           exposure=implied, packet=builder.build(), confidence=Decimal("0.8"))


@register
class HoldbackNotRetained(Rule):
    rule_id = "FOS-R059"
    version = "1"
    title = "Statutory holdback not retained on trade payments"
    purpose = "Ontario's Construction Act requires a 10% holdback on each trade contract until 60 days after substantial performance; paying trades in full exposes the owner to lien claims twice."
    category = "development"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Retain 10% of each trade payment to the holdback account; recover overpayments by short-paying the next progress bill."
    evidence_required = ("trade bill payments", "holdback postings")
    references = ("Construction Act (Ontario) s.22",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not _in_scope(ctx):
            return
        acc = _accounts(ctx)
        pct = Decimal(str(ctx.policy.get("holdback_percent", 10))) / Decimal(100)
        hard_bills = {}
        for t in ctx.ledger.transactions:
            if ctx.period_start <= t.txn_date <= ctx.period_end and t.type is TxnType.BILL:
                net = Money.zero(ctx.currency)
                for ln in t.lines:
                    if ln.account_id == acc["hard_costs"] and ln.amount.minor_units > 0:
                        net = net + ln.amount
                if net.minor_units:
                    hard_bills[t.party_id] = hard_bills.get(t.party_id, Money.zero(ctx.currency)) + net
        if not hard_bills:
            return
        retained: dict[str | None, Money] = {}
        for t, ln in ctx.ledger.postings(acc["holdback_payable"]):
            if ctx.period_start <= t.txn_date <= ctx.period_end and ln.amount.minor_units < 0:
                retained[t.party_id] = retained.get(t.party_id, Money.zero(ctx.currency)) + (-ln.amount)
        for party_id, net in hard_bills.items():
            expected = net.scale(pct)
            actual = retained.get(party_id, Money.zero(ctx.currency))
            short = expected - actual
            if short < ctx.materiality.trivial or short.minor_units <= 0:
                continue
            party = ctx.ledger.parties.get(party_id) if party_id else None
            builder = ctx.packet(f"{self.rule_id}-{party_id}", f"Holdback retained against {party.name if party else party_id}")
            for t in [t for t in ctx.ledger.transactions if t.party_id == party_id and ctx.period_start <= t.txn_date <= ctx.period_end][:10]:
                builder.transaction(t, label="Trade bill or payment")
            builder.calc("hard-cost billings in period", "sum(net hard cost lines)", net)
            builder.calc("holdback required", f"billings x {pct:.0%}", expected)
            builder.calc("holdback retained", "credits to holdback payable", actual)
            builder.context(vendor=party_id)
            yield self.finding(ctx, suffix=str(party_id), title=f"{short.format()} of holdback not retained from {party.name if party else party_id}",
                               narrative=f"{net.format()} of hard costs were billed by {party.name if party else party_id}; {expected.format()} should be held back and {actual.format()} was.",
                               exposure=short, packet=builder.build())


@register
class DrawExceedsFacility(Rule):
    rule_id = "FOS-R060"
    version = "1"
    title = "Construction loan balance exceeds the facility limit"
    purpose = "A draw beyond the approved facility is either a posting error or a lender conversation the owner has not had."
    category = "development"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = "Agree the loan balance to the lender statement; if the balance is right, document the increase in facility."
    evidence_required = ("loan postings", "facility limits")
    involves_disbursed_cash = True

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not _in_scope(ctx):
            return
        limits: dict[str, str] = ctx.policy.get("facility_limits", {})
        if not limits:
            return
        for account_id, limit_s in limits.items():
            acct = ctx.ledger.account(account_id)
            if acct is None:
                continue
            balance = Money.zero(ctx.currency)
            for row in ctx.tb.rows:
                if row.account.account_id == account_id:
                    balance = -row.closing
            limit = Money.from_decimal(str(limit_s), ctx.currency)
            if balance <= limit:
                continue
            excess = balance - limit
            builder = ctx.packet(f"{self.rule_id}-{account_id}", f"Balance of {acct.name} against its limit")
            for t, _ln in [(t, ln) for t, ln in ctx.ledger.postings(account_id) if ctx.period_start <= t.txn_date <= ctx.period_end][:10]:
                builder.transaction(t, label="Draw")
            builder.calc("loan balance", "closing credit balance", balance)
            builder.calc("facility limit", "policy", limit)
            builder.calc("excess", "balance - limit", excess)
            builder.context(account=account_id)
            yield self.finding(ctx, suffix=account_id, title=f"{acct.name} is {excess.format()} over its {limit.format()} limit",
                               narrative=f"The ledger shows {balance.format()} drawn against a facility of {limit.format()}.", exposure=excess, packet=builder.build())


@register
class CostPostedToLandAfterConstructionStart(Rule):
    rule_id = "FOS-R061"
    version = "1"
    title = "Cost posted to land after construction began"
    purpose = "Land is not depreciable and is valued separately by the appraiser; construction costs that land on it are lost to CCA and misstate both figures."
    category = "development"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R1
    remediation = "Move the posting to the building under construction account it belongs to."
    evidence_required = ("land postings", "construction start date")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not _in_scope(ctx):
            return
        acc = _accounts(ctx)
        start_raw = ctx.policy.get("construction_start_date")
        start = date.fromisoformat(start_raw) if isinstance(start_raw, str) else start_raw
        if start is None:
            return
        rows = [(t, ln) for t, ln in ctx.ledger.postings(acc["land"])
                if max(ctx.period_start, start) <= t.txn_date <= ctx.period_end and ln.amount.minor_units > 0 and t.type is not TxnType.OPENING_BALANCE]
        if not rows:
            return
        total = Money.zero(ctx.currency)
        for _t, ln in rows:
            total = total + ln.amount
        if total < ctx.materiality.trivial:
            return
        builder = ctx.packet(f"{self.rule_id}-period", "Postings to land after the construction start date")
        for t, _ln in rows[:10]:
            builder.transaction(t, label="Posted to land")
        builder.calc("posted to land after construction start", "sum(debits)", total)
        builder.context(construction_start=str(start))
        yield self.finding(ctx, suffix=ctx.period_end.strftime("%Y%m"), title=f"{total.format()} posted to land after construction began on {start}",
                           narrative=f"{len(rows)} posting(s) totalling {total.format()} went to land after {start}. Costs from that date belong to the building.",
                           exposure=total, packet=builder.build(), confidence=Decimal("0.85"))


@register
class SoftCostsExpensedDuringConstruction(Rule):
    rule_id = "FOS-R062"
    version = "1"
    title = "Soft costs expensed during construction"
    purpose = "Architects, engineers, permits, development charges, legal and builder's-risk insurance during the build are capitalised (ITA s.18(3.1)); expensing them creates a deduction the Act denies."
    category = "development"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R1
    remediation = "Reclassify to building under construction: soft costs."
    evidence_required = ("operating expense postings", "vendor names")
    references = ("ITA s.18(3.1)",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not _in_scope(ctx) or not _under_construction(ctx):
            return
        suspects = []
        for t in ctx.ledger.transactions:
            if not (ctx.period_start <= t.txn_date <= ctx.period_end) or t.type not in (TxnType.BILL, TxnType.EXPENSE, TxnType.CHEQUE):
                continue
            party = ctx.ledger.parties.get(t.party_id) if t.party_id else None
            text = f"{party.name if party else ''} {t.memo or ''}".lower()
            if not any(w in text for w in SOFT_COST_WORDS):
                continue
            for ln in t.lines:
                acct = ctx.ledger.account(ln.account_id)
                if acct and acct.subtype is AccountSubtype.OPERATING_EXPENSE and ln.amount.minor_units > 0:
                    suspects.append((t, ln))
        if not suspects:
            return
        total = Money.zero(ctx.currency)
        for _t, ln in suspects:
            total = total + ln.amount
        if total < ctx.materiality.trivial:
            return
        builder = ctx.packet(f"{self.rule_id}-period", "Soft-cost vendors posted to operating expense during construction")
        for t, _ln in suspects[:10]:
            builder.transaction(t, label="Soft cost expensed")
        builder.calc("soft costs expensed", "sum(operating expense lines from soft-cost vendors)", total)
        yield self.finding(ctx, suffix=ctx.period_end.strftime("%Y%m"), title=f"{total.format()} of soft costs expensed during construction",
                           narrative=f"{len(suspects)} posting(s) from architects, engineers, permit authorities, lawyers or insurers went to operating expense while the building is under construction.",
                           exposure=total, packet=builder.build(), confidence=Decimal("0.8"))
