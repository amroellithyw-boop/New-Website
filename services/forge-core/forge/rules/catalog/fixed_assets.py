"""Controls 40-44: fixed assets, depreciation, prepaids and accrual hygiene."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from ...canonical.enums import AccountSubtype, RiskTier, Severity, TxnType
from ...engine.rollforward import build_rollforward
from ...evidence.packet import EvidenceRef
from ...money import msum
from ..base import Finding, Rule, RuleContext, register


@register
class UnreviewedAssetAdditions(Rule):
    rule_id = "FOS-R040"
    version = "1"
    title = "Fixed asset addition without capitalisation review"
    purpose = (
        "Every addition needs a useful life, an in-service date and a depreciation "
        "method before it can be depreciated correctly or claimed for tax."
    )
    category = "fixed assets"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Add the asset to the register with its in-service date, useful life and "
        "capital cost allowance class, then start depreciation."
    )
    evidence_required = ("addition transaction", "purchase invoice", "asset register entry")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.FIXED_ASSET):
            additions = [
                (t, line)
                for (t, line) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
                and line.amount.minor_units > 0
                and t.type is not TxnType.OPENING_BALANCE
            ]
            for txn, line in additions:
                if ctx.materiality.is_trivial(line.amount):
                    continue
                if txn.document_refs:
                    continue
                packet = (
                    ctx.packet(
                        f"{self.rule_id}-{line.line_id}",
                        f"Show the addition to {acct.name} and the missing support",
                    )
                    .transaction(txn)
                    .calc("addition amount", "line amount", line.amount)
                    .calc("supporting documents attached", "count", len(txn.document_refs))
                    .build()
                )
                yield self.finding(
                    ctx,
                    suffix=line.line_id,
                    title=f"{line.amount.format()} added to {acct.name} without support",
                    narrative=(
                        f"An addition of {line.amount.format()} on {txn.txn_date.isoformat()} has "
                        "no attached invoice, so useful life, in-service date and tax class "
                        "cannot be determined."
                    ),
                    exposure=line.amount,
                    packet=packet,
                )


@register
class FixedAssetRollForwardTie(Rule):
    rule_id = "FOS-R041"
    version = "1"
    title = "Fixed asset roll-forward does not tie"
    purpose = "Prove opening plus additions less disposals equals the closing balance."
    category = "fixed assets"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Rebuild the asset register and reconcile it to the ledger account."
    evidence_required = ("asset register", "account roll-forward")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.FIXED_ASSET):
            rf = build_rollforward(ctx.ledger, acct.account_id, ctx.period_start, ctx.period_end)
            if rf.ties:
                continue
            packet = (
                ctx.packet(f"{self.rule_id}-{acct.account_id}", f"Roll forward {acct.name}")
                .calc("opening balance", "balance at period start", rf.opening)
                .calc("movements", "sum(period postings)", rf.total_movement)
                .calc("closing balance", "balance at period end", rf.closing)
                .calc("difference", "opening + movement - closing", rf.difference)
                .build()
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{acct.name} roll-forward is out by {abs(rf.difference).format()}",
                narrative=(
                    f"Opening {rf.opening.format()} plus movement {rf.total_movement.format()} "
                    f"does not reach closing {rf.closing.format()}."
                ),
                exposure=abs(rf.difference),
                packet=packet,
            )


@register
class DepreciationReasonableness(Rule):
    rule_id = "FOS-R042"
    version = "1"
    title = "Depreciation charge is not reasonable against the asset base"
    purpose = (
        "Recompute an expected annual charge from the gross asset base and compare "
        "it with what was recorded. Catches both a stopped charge and a doubled one."
    )
    category = "fixed assets"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Rebuild the depreciation schedule per asset and correct the period charge."
    )
    evidence_required = ("asset register", "depreciation entries", "accumulated depreciation")

    TOLERANCE = Decimal("0.40")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        gross = msum(
            (
                r.presentation_balance
                for r in ctx.tb.rows_of(subtype=AccountSubtype.FIXED_ASSET)
            ),
            ctx.currency,
        )
        if gross.minor_units <= 0:
            return
        charge = msum(
            (
                r.presentation_movement
                for r in ctx.tb.rows_of(subtype=AccountSubtype.DEPRECIATION_EXPENSE)
            ),
            ctx.currency,
        )
        days = max((ctx.period_end - ctx.period_start).days + 1, 1)
        rate = ctx.policy.get("depreciation_rate", Decimal("0.15"))
        expected = gross.scale(rate * Decimal(days) / Decimal(365))
        if expected.minor_units == 0:
            return
        variance = charge - expected
        ratio = abs(variance).ratio_to(expected)
        if ratio is None or ratio <= self.TOLERANCE:
            return
        if ctx.materiality.is_trivial(variance):
            return
        builder = ctx.packet(
            f"{self.rule_id}-dep", "Recompute the expected depreciation charge"
        )
        for row in ctx.tb.rows_of(subtype=AccountSubtype.FIXED_ASSET):
            if row.presentation_balance.is_zero:
                continue
            builder.ref(
                EvidenceRef(
                    kind="account",
                    ref_id=row.account.account_id,
                    label=f"{row.account.number} {row.account.name}",
                    amount=row.presentation_balance,
                    on=ctx.period_end,
                )
            )
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.DEPRECIATION_EXPENSE):
            for txn, _line in ctx.drivers(
                acct.account_id, start=ctx.period_start - timedelta(days=120)
            )[:6]:
                builder.transaction(txn, label="Depreciation entry in recent history")
        packet = (
            builder
            .calc("gross fixed assets", "sum(fixed asset closing balances)", gross)
            .calc("assumed annual rate", "policy depreciation rate", rate)
            .calc("days in period", "period end - period start + 1", days)
            .calc("expected charge", f"gross x {rate} x days / 365", expected)
            .calc("recorded charge", "movement in depreciation expense", charge)
            .calc("variance", "recorded - expected", variance)
            .build()
        )
        direction = "below" if variance.minor_units < 0 else "above"
        yield self.finding(
            ctx,
            suffix="depreciation",
            title=f"Depreciation is {abs(variance).format()} {direction} expectation",
            narrative=(
                f"A gross asset base of {gross.format()} at {rate:.0%} implies about "
                f"{expected.format()} for this period, against {charge.format()} recorded."
            ),
            exposure=abs(variance),
            packet=packet,
            confidence=Decimal("0.55"),
        )


@register
class PrepaidRollForward(Rule):
    rule_id = "FOS-R043"
    version = "1"
    title = "Prepaid balance is not being amortised"
    purpose = (
        "A prepaid that only ever grows means the expense is sitting on the balance "
        "sheet and profit is overstated."
    )
    category = "close"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Build the amortisation schedule and post the catch-up charge for the periods "
        "already elapsed."
    )
    evidence_required = ("prepaid roll-forward", "underlying policy or contract")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.PREPAID_EXPENSE):
            rf = build_rollforward(ctx.ledger, acct.account_id, ctx.period_start, ctx.period_end)
            additions = msum(
                (m.amount for m in rf.movements if m.amount.minor_units > 0), ctx.currency
            )
            releases = msum(
                (-m.amount for m in rf.movements if m.amount.minor_units < 0), ctx.currency
            )
            if rf.closing.minor_units <= 0:
                continue
            if not releases.is_zero:
                continue
            if ctx.materiality.is_trivial(rf.closing):
                continue
            packet = (
                ctx.packet(f"{self.rule_id}-{acct.account_id}", f"Roll forward {acct.name}")
                .calc("opening balance", "balance at period start", rf.opening)
                .calc("additions", "sum(debits in period)", additions)
                .calc("amortised to expense", "sum(credits in period)", releases)
                .calc("closing balance", "balance at period end", rf.closing)
                .build()
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{acct.name} of {rf.closing.format()} was not amortised this period",
                narrative=(
                    f"{acct.name} carries {rf.closing.format()} with no amortisation posted in the "
                    "period. The related expense is missing from profit."
                ),
                exposure=rf.closing,
                packet=packet,
            )


@register
class AccrualReversalHygiene(Rule):
    rule_id = "FOS-R044"
    version = "1"
    title = "Accrual not reversed, or reversed twice"
    purpose = (
        "An accrual that is never reversed double-counts the cost when the invoice "
        "arrives. One reversed twice understates it."
    )
    category = "close"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Post the missing reversal, or remove the duplicate one, in the period it belongs to."
    evidence_required = ("accrual entry", "reversal entry", "subsequent invoice")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        accrual_accounts = {
            a.account_id for a in ctx.ledger.accounts_of(subtype=AccountSubtype.ACCRUED_LIABILITY)
        }
        if not accrual_accounts:
            return
        reversal_counts: dict[str, int] = {}
        for t in ctx.ledger.transactions:
            if t.reverses_txn_id:
                reversal_counts[t.reverses_txn_id] = reversal_counts.get(t.reverses_txn_id, 0) + 1

        lookback = ctx.period_start - timedelta(days=95)
        for txn in ctx.ledger.transactions:
            if not txn.is_adjusting or txn.reverses_txn_id:
                continue
            if not (lookback <= txn.txn_date < ctx.period_start):
                continue
            if not any(line.account_id in accrual_accounts for line in txn.lines):
                continue
            count = reversal_counts.get(txn.txn_id, 0)
            if count == 1:
                continue
            amount = txn.absolute_value
            if ctx.materiality.is_trivial(amount):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{txn.txn_id}", "Show the accrual and its reversals"
            )
            builder.transaction(txn, label="Original accrual")
            for rev in ctx.ledger.transactions:
                if rev.reverses_txn_id == txn.txn_id:
                    builder.transaction(rev, label="Reversal")
            builder.calc("accrual amount", "entry value", amount)
            builder.calc("reversals found", "count", count)
            problem = "was never reversed" if count == 0 else f"was reversed {count} times"
            yield self.finding(
                ctx,
                suffix=txn.txn_id,
                title=f"Accrual of {amount.format()} {problem}",
                narrative=(
                    f"Accrual {txn.doc_number or txn.txn_id} dated {txn.txn_date.isoformat()} "
                    f"{problem}. Costs are "
                    + ("double counted" if count == 0 else "understated")
                    + " by up to "
                    + amount.format()
                    + "."
                ),
                exposure=amount,
                packet=builder.build(),
            )
