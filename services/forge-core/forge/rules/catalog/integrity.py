"""Controls 1-5 and 11-19: ledger integrity, period control and classification.

These are the controls that decide whether anything else in the system can be
believed. If the trial balance does not tie, every downstream number is fiction,
so these run first and their failures are CRITICAL by construction.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from ...canonical.enums import AccountSubtype, AccountType, RiskTier, Severity, Side, TxnType
from ...engine.features import is_round_amount
from ...evidence.packet import EvidenceRef
from ...money import Money
from ..base import Finding, Rule, RuleContext, register


@register
class BalanceSheetEquation(Rule):
    rule_id = "FOS-R001"
    version = "1"
    title = "Balance sheet equation and trial balance integrity"
    purpose = (
        "Prove that assets equal liabilities plus equity and that total debits "
        "equal total credits. Nothing else in the close means anything until this holds."
    )
    category = "integrity"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Do not proceed with the close. Trace the imbalance to its source entry, "
        "correct it in the ledger, and re-run the tie-out before any other review."
    )
    evidence_required = ("trial balance", "balance sheet")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        if not ctx.tb.closing_imbalance.is_zero:
            diff = ctx.tb.closing_imbalance
            builder = ctx.packet(f"{self.rule_id}-tb", "Prove the trial balance nets to zero")
            for txn in ctx.tb.unbalanced_transactions[:15]:
                builder.transaction(txn, label="Entry whose debits do not equal its credits")
            for row in sorted(
                ctx.tb.nonzero_rows(), key=lambda r: -abs(r.closing).minor_units
            )[:10]:
                builder.ref(
                    EvidenceRef(
                        kind="account",
                        ref_id=row.account.account_id,
                        label=f"{row.account.number} {row.account.name}",
                        amount=row.closing,
                        on=ctx.period_end,
                    )
                )
            packet = (
                builder
                .calc(
                    "trial balance net",
                    "sum(closing balance of every account, debit-positive)",
                    diff,
                    account_count=len(ctx.tb.rows),
                )
                .calc("total debits", "sum(debit amounts in period)", ctx.tb.total_debits)
                .calc("total credits", "sum(credit amounts in period)", ctx.tb.total_credits)
                .context(period=ctx.period_label())
                .build()
            )
            yield self.finding(
                ctx,
                suffix="trial-balance",
                title="Trial balance does not net to zero",
                narrative=(
                    f"The sum of all closing balances is {diff.format()} rather than zero. "
                    "A double-entry ledger cannot produce this; either an entry was posted "
                    "one-sided or the data import is incomplete."
                ),
                exposure=abs(diff),
                packet=packet,
            )

        if not ctx.bs.equation_difference.is_zero:
            diff = ctx.bs.equation_difference
            builder = ctx.packet(f"{self.rule_id}-bs", "Prove assets = liabilities + equity")
            for section in (
                ctx.bs.current_assets, ctx.bs.fixed_assets, ctx.bs.other_assets,
                ctx.bs.current_liabilities, ctx.bs.long_term_liabilities, ctx.bs.equity,
            ):
                for row in section.nonzero()[:4]:
                    builder.ref(
                        EvidenceRef(
                            kind="account",
                            ref_id=row.account.account_id,
                            label=f"{section.label}: {row.account.number} {row.account.name}",
                            amount=section.value_of(row),
                            on=ctx.period_end,
                        )
                    )
            packet = (
                builder
                .calc("total assets", "current + fixed + other assets", ctx.bs.total_assets)
                .calc(
                    "total liabilities",
                    "current + long-term liabilities",
                    ctx.bs.total_liabilities,
                )
                .calc(
                    "total equity",
                    "equity accounts + prior-period earnings + current-period net income",
                    ctx.bs.total_equity,
                )
                .calc("difference", "assets - (liabilities + equity)", diff)
                .build()
            )
            yield self.finding(
                ctx,
                suffix="equation",
                title="Balance sheet does not balance",
                narrative=(
                    f"Assets exceed liabilities plus equity by {diff.format()}. "
                    "Every reported figure downstream of this is unreliable."
                ),
                exposure=abs(diff),
                packet=packet,
            )


@register
class StatementRollupTie(Rule):
    rule_id = "FOS-R002"
    version = "1"
    title = "Profit and loss ties to the movement in retained earnings"
    purpose = (
        "Confirm the income statement built from period movement agrees with the "
        "change in net assets over the same period. Catches a P&L assembled from "
        "the wrong balances."
    )
    category = "integrity"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = "Rebuild the statements from period movement and re-run the tie-out."
    evidence_required = ("trial balance", "income statement")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        # Net income must equal revenue movement less expense movement. Both are
        # presented positive, so expenses are subtracted rather than added; adding
        # them would make the control pass only when profit happened to be zero.
        movement = Money.zero(ctx.currency)
        for row in ctx.tb.rows:
            if row.account.type is AccountType.REVENUE:
                movement = movement + row.presentation_movement
            elif row.account.type is AccountType.EXPENSE:
                movement = movement - row.presentation_movement
        diff = ctx.pl.net_income - movement
        if diff.is_zero:
            return
        builder = ctx.packet(f"{self.rule_id}-tie", "Tie net income to profit and loss movement")
        for row in sorted(
            (r for r in ctx.tb.rows if r.account.type.is_income_statement),
            key=lambda r: -abs(r.movement).minor_units,
        )[:12]:
            builder.ref(
                EvidenceRef(
                    kind="account",
                    ref_id=row.account.account_id,
                    label=f"{row.account.number} {row.account.name}",
                    amount=row.presentation_movement,
                    on=ctx.period_end,
                )
            )
        packet = (
            builder
            .calc("net income per statement", "revenue - cost of sales - expenses", ctx.pl.net_income)
            .calc(
                "net movement of profit and loss accounts",
                "sum(period movement, credit-positive for revenue)",
                movement,
            )
            .calc("difference", "net income - movement", diff)
            .build()
        )
        yield self.finding(
            ctx,
            suffix="rollup",
            title="Income statement does not tie to ledger movement",
            narrative=(
                f"The reported net income differs from the underlying account movement by "
                f"{diff.format()}. An account is missing from the statement mapping."
            ),
            exposure=abs(diff),
            packet=packet,
        )


@register
class RetainedEarningsContinuity(Rule):
    rule_id = "FOS-R003"
    version = "1"
    title = "Retained earnings moved without a closing entry"
    purpose = (
        "Retained earnings should change only through the year-end close or a "
        "deliberate equity adjustment. Direct postings are almost always errors."
    )
    category = "integrity"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Reverse the direct posting and record the transaction in its proper account. "
        "If the entry was a genuine prior-period adjustment, document the reason."
    )
    evidence_required = ("retained earnings postings",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.RETAINED_EARNINGS):
            hits = [
                (t, line)
                for (t, line) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
                and t.type is not TxnType.OPENING_BALANCE
            ]
            if not hits:
                continue
            total = Money.zero(ctx.currency)
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}",
                "Identify direct postings to retained earnings",
            )
            for txn, line in hits:
                total = total + line.amount
                builder.transaction(txn, label=f"Posting to {acct.name}")
            builder.calc(
                "net direct movement",
                "sum(postings to retained earnings excluding opening balance)",
                total,
                entry_count=len(hits),
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{len(hits)} direct posting(s) to retained earnings",
                narrative=(
                    f"{acct.name} moved by {total.format()} during the period through "
                    f"{len(hits)} entr{'y' if len(hits) == 1 else 'ies'} that were not a "
                    "year-end close. Prior-period results are being restated silently."
                ),
                exposure=abs(total),
                packet=builder.build(),
            )


@register
class UnbalancedEntries(Rule):
    rule_id = "FOS-R004"
    version = "1"
    title = "Out-of-balance journal entries"
    purpose = "Detect entries whose debits do not equal credits."
    category = "integrity"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = "Correct the entry so debits equal credits, then re-run the trial balance."
    evidence_required = ("transaction lines",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for txn in ctx.tb.unbalanced_transactions:
            packet = (
                ctx.packet(f"{self.rule_id}-{txn.txn_id}", "Prove the entry is out of balance")
                .transaction(txn)
                .calc("total debits", "sum(positive line amounts)", txn.total_debits)
                .calc("total credits", "sum(negative line amounts, absolute)", txn.total_credits)
                .calc("imbalance", "debits - credits", txn.imbalance)
                .build()
            )
            yield self.finding(
                ctx,
                suffix=txn.txn_id,
                title=f"Entry {txn.doc_number or txn.txn_id} is out of balance",
                narrative=(
                    f"Debits exceed credits by {txn.imbalance.format()} on "
                    f"{txn.txn_date.isoformat()}."
                ),
                exposure=abs(txn.imbalance),
                packet=packet,
            )


@register
class DuplicateSourceIds(Rule):
    rule_id = "FOS-R005"
    version = "1"
    title = "Duplicate source identifiers"
    purpose = (
        "A source system identifier must map to exactly one canonical transaction. "
        "Two transactions sharing one identifier means the sync double-imported."
    )
    category = "integrity"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Do not correct this in the ledger. Fix the connector's idempotency key and "
        "re-run the sync, then confirm the duplicate canonical records are gone."
    )
    evidence_required = ("transaction lineage",)

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        seen: dict[str, list] = {}
        for txn in ctx.ledger.transactions:
            if txn.lineage is None:
                continue
            seen.setdefault(txn.lineage.key, []).append(txn)
        for key, group in seen.items():
            if len(group) < 2:
                continue
            exposure = group[0].absolute_value.scale(len(group) - 1)
            builder = ctx.packet(
                f"{self.rule_id}-{key}", "Show the transactions sharing one source id"
            )
            builder.transactions(group).calc(
                "duplicate copies", "count(transactions) - 1", len(group) - 1, source_key=key
            ).calc("value duplicated", "entry value x extra copies", exposure)
            yield self.finding(
                ctx,
                suffix=key.replace(":", "-"),
                title=f"Source identifier {key} imported {len(group)} times",
                narrative=(
                    f"{len(group)} canonical transactions carry the same source identifier. "
                    f"Up to {exposure.format()} is recorded more than once."
                ),
                exposure=exposure,
                packet=builder.build(),
            )


@register
class ClosedPeriodPostings(Rule):
    rule_id = "FOS-R011"
    version = "1"
    title = "Transactions posted into a closed period"
    purpose = (
        "Once a period is closed, statements have been issued and tax may have been "
        "filed. Any entry landing there changes a number someone already relied on."
    )
    category = "period control"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R4
    remediation = (
        "Reverse the entry from the closed period and re-post it in the current open "
        "period. If the closed period genuinely must change, reissue the affected "
        "statements and assess the filing impact."
    )
    evidence_required = ("transaction", "period close status")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        offenders = []
        for txn in ctx.ledger.transactions:
            if txn.type is TxnType.OPENING_BALANCE:
                continue
            if not ctx.is_in_closed_period(txn.txn_date):
                continue
            created = txn.created_at.date() if txn.created_at else txn.txn_date
            period = ctx.ledger.calendar.period_for(txn.txn_date)
            closed_at = period.closed_at.date() if period and period.closed_at else None
            # Entered after the period closed, or entered well after the fact.
            if closed_at is not None and created <= closed_at:
                continue
            if closed_at is None and created <= txn.txn_date + timedelta(days=30):
                continue
            offenders.append(txn)

        if not offenders:
            return
        total = Money.zero(ctx.currency)
        builder = ctx.packet(f"{self.rule_id}-closed", "List entries dated in closed periods")
        for txn in sorted(offenders, key=lambda t: -t.absolute_value.minor_units)[:25]:
            builder.transaction(txn, label=f"Posted into closed period {txn.txn_date}")
        for txn in offenders:
            total = total + txn.absolute_value
        builder.calc("entries in closed periods", "count", len(offenders))
        builder.calc("total value", "sum(entry value)", total)
        yield self.finding(
            ctx,
            suffix="closed-period",
            title=f"{len(offenders)} entr{'y' if len(offenders) == 1 else 'ies'} posted into a closed period",
            narrative=(
                f"{total.format()} was posted into periods already closed. Issued statements "
                "and any filings based on them no longer agree with the ledger."
            ),
            exposure=total,
            packet=builder.build(),
        )


@register
class PostCloseModifications(Rule):
    rule_id = "FOS-R012"
    version = "1"
    title = "Prior-period entries modified after the fact"
    purpose = "Detect edits to transactions dated in a period that has been closed."
    category = "period control"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Determine what changed and why. Restate the affected period or reverse the "
        "modification, and lock the period in the source system."
    )
    evidence_required = ("transaction", "modification timestamp")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        offenders = [
            t
            for t in ctx.ledger.transactions
            if t.last_modified_at is not None
            and ctx.is_in_closed_period(t.txn_date)
            and t.last_modified_at.date() > t.txn_date + timedelta(days=45)
        ]
        if not offenders:
            return
        total = Money.zero(ctx.currency)
        builder = ctx.packet(f"{self.rule_id}-mods", "Entries edited after their period closed")
        for txn in offenders[:25]:
            builder.transaction(
                txn,
                label=f"Dated {txn.txn_date}, last edited {txn.last_modified_at.date()}",
            )
        for txn in offenders:
            total = total + txn.absolute_value
        builder.calc("modified entries", "count", len(offenders)).calc(
            "total value", "sum(entry value)", total
        )
        yield self.finding(
            ctx,
            suffix="post-close-edit",
            title=f"{len(offenders)} closed-period entr{'y' if len(offenders) == 1 else 'ies'} edited after close",
            narrative=(
                f"{total.format()} of transactions dated in closed periods were edited well "
                "after those periods closed. Reported results have changed since issue."
            ),
            exposure=total,
            packet=builder.build(),
        )


@register
class PeriodEndManualEntries(Rule):
    rule_id = "FOS-R013"
    version = "1"
    title = "Manual journal entries concentrated at period end"
    purpose = (
        "Manual entries dated in the last days of a period are the classic vehicle "
        "for earnings management and for plugging a difference nobody understood."
    )
    category = "period control"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Obtain support for each entry. Confirm the amount is calculated rather than "
        "estimated, and that the entry reverses if it is an accrual."
    )
    evidence_required = ("journal entry", "supporting schedule")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        window_start = ctx.period_end - timedelta(days=3)
        hits = [
            t
            for t in ctx.ledger.transactions
            if t.is_manual
            and window_start <= t.txn_date <= ctx.period_end
            and t.type is TxnType.JOURNAL_ENTRY
            and not t.is_adjusting
        ]
        if not hits:
            return
        total = Money.zero(ctx.currency)
        for t in hits:
            total = total + t.absolute_value
        if ctx.materiality.is_trivial(total):
            return
        builder = ctx.packet(
            f"{self.rule_id}-periodend", "Manual entries in the final days of the period"
        )
        builder.transactions(sorted(hits, key=lambda t: -t.absolute_value.minor_units)[:20])
        builder.calc("entries", "count(manual journal entries in last 3 days)", len(hits))
        builder.calc("total value", "sum(entry value)", total)
        builder.calc(
            "share of period profit",
            "total value / net income",
            total.ratio_to(abs(ctx.pl.net_income)) or Decimal(0),
        )
        yield self.finding(
            ctx,
            suffix="period-end-manual",
            title=f"{len(hits)} manual journal entr{'y' if len(hits) == 1 else 'ies'} dated in the last three days",
            narrative=(
                f"{total.format()} was booked by manual journal entry immediately before period "
                "end. Each needs support showing it was calculated, not estimated to hit a number."
            ),
            exposure=total,
            packet=builder.build(),
            confidence=Decimal("0.6"),
        )


@register
class RoundDollarManualEntries(Rule):
    rule_id = "FOS-R014"
    version = "1"
    title = "High-value round-dollar manual entries"
    purpose = (
        "Real transactions rarely land on an exact thousand. A large round manual "
        "entry is usually an estimate, a plug, or a placeholder nobody replaced."
    )
    category = "period control"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Replace the estimate with a calculated amount supported by a schedule, or "
        "document the basis for the estimate and the date it will be trued up."
    )
    evidence_required = ("journal entry", "calculation basis")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        threshold = max(ctx.materiality.performance.minor_units, 100_000)
        for txn in ctx.ledger.transactions:
            if not (ctx.period_start <= txn.txn_date <= ctx.period_end):
                continue
            if not txn.is_manual or txn.is_adjusting:
                continue
            value = txn.absolute_value
            if value.minor_units < threshold or not is_round_amount(value):
                continue
            packet = (
                ctx.packet(f"{self.rule_id}-{txn.txn_id}", "Round-dollar manual entry")
                .transaction(txn)
                .calc("entry value", "sum(debits)", value)
                .calc(
                    "performance materiality",
                    ctx.materiality.describe(),
                    ctx.materiality.performance,
                )
                .build()
            )
            yield self.finding(
                ctx,
                suffix=txn.txn_id,
                title=f"Round-dollar manual entry of {value.format()}",
                narrative=(
                    f"Entry {txn.doc_number or txn.txn_id} posts exactly {value.format()} on "
                    f"{txn.txn_date.isoformat()}. Round amounts at this size are estimates "
                    "until proven otherwise."
                ),
                exposure=value,
                packet=packet,
                confidence=Decimal("0.55"),
            )


@register
class UnexpectedPoster(Rule):
    rule_id = "FOS-R015"
    version = "1"
    title = "Entries posted by an unexpected user or integration"
    purpose = (
        "Segregation of duties is only real if you know who posted what. An entry "
        "from an unrecognised actor is a control gap regardless of its amount."
    )
    category = "period control"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Confirm the actor is authorised. Remove access that is no longer needed and "
        "review everything that actor posted in the period."
    )
    evidence_required = ("transaction", "authorised actor list")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        expected = set(ctx.policy.get("authorised_posters", ()))
        if not expected:
            return
        by_actor: dict[str, list] = {}
        for txn in ctx.ledger.transactions:
            if not (ctx.period_start <= txn.txn_date <= ctx.period_end):
                continue
            actor = txn.created_by or "unknown"
            if actor in expected:
                continue
            by_actor.setdefault(actor, []).append(txn)
        for actor, txns in by_actor.items():
            total = Money.zero(ctx.currency)
            for t in txns:
                total = total + t.absolute_value
            builder = ctx.packet(
                f"{self.rule_id}-{actor}", f"Entries posted by {actor}"
            )
            builder.transactions(sorted(txns, key=lambda t: -t.absolute_value.minor_units)[:20])
            builder.calc("entries", "count", len(txns)).calc("total value", "sum", total)
            builder.context(authorised_posters=sorted(expected))
            yield self.finding(
                ctx,
                suffix=actor,
                title=f"{len(txns)} entr{'y' if len(txns) == 1 else 'ies'} posted by unrecognised actor '{actor}'",
                narrative=(
                    f"'{actor}' is not on the authorised poster list yet posted "
                    f"{total.format()} this period."
                ),
                exposure=total,
                packet=builder.build(),
            )


@register
class AbnormalSidePostings(Rule):
    rule_id = "FOS-R016"
    version = "1"
    title = "Postings on the side an account does not normally take"
    purpose = (
        "A credit to an expense or a debit to revenue is either a correction, a "
        "refund, or a misposting. Each needs to be one of those on purpose."
    )
    category = "classification"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Confirm the posting is a genuine credit note or refund. If it is a "
        "correction, ensure the original error was also fixed."
    )
    evidence_required = ("transaction line", "account normal balance")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for acct in ctx.ledger.accounts.values():
            if not acct.type.is_income_statement:
                continue
            wrong_side = Side.CREDIT if acct.normal_balance is Side.DEBIT else Side.DEBIT
            hits = [
                (t, line)
                for (t, line) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
                and line.side is wrong_side
                and t.reverses_txn_id is None
            ]
            if not hits:
                continue
            total = Money.zero(ctx.currency)
            for _t, line in hits:
                total = total + abs(line.amount)
            if ctx.materiality.is_trivial(total):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}",
                f"Postings to {acct.name} on the {wrong_side.value} side",
            )
            for txn, _l in sorted(hits, key=lambda p: -abs(p[1].amount).minor_units)[:15]:
                builder.transaction(txn)
            builder.calc("postings on unusual side", "count", len(hits))
            builder.calc("total value", "sum(absolute line amount)", total)
            builder.calc(
                "account normal balance", "from chart of accounts", acct.normal_balance.value
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{acct.name} has {len(hits)} {wrong_side.value} posting(s)",
                narrative=(
                    f"{total.format()} was posted to {acct.name} on the {wrong_side.value} side, "
                    f"which is not its normal balance."
                ),
                exposure=total,
                packet=builder.build(),
                confidence=Decimal("0.5"),
            )


@register
class AbnormalAccountBalances(Rule):
    rule_id = "FOS-R017"
    version = "1"
    title = "Accounts carrying a balance on the wrong side"
    purpose = (
        "A negative bank balance, a debit in accounts payable, or a credit in a "
        "fixed asset each point to a specific, findable error."
    )
    category = "classification"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Trace the account back to the entry that pushed it across zero and correct "
        "the classification or the missing offsetting entry."
    )
    evidence_required = ("trial balance row", "account postings")

    IGNORE = {
        AccountSubtype.ACCUMULATED_DEPRECIATION,
        AccountSubtype.OWNER_DRAWS,
        AccountSubtype.RETAINED_EARNINGS,
        AccountSubtype.SALES_TAX_PAYABLE,
        AccountSubtype.CREDIT_CARD,
    }

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for row in ctx.tb.rows:
            acct = row.account
            if acct.type.is_income_statement or acct.subtype in self.IGNORE:
                continue
            if not row.is_abnormal or row.closing.is_zero:
                continue
            amount = abs(row.closing)
            if ctx.materiality.is_trivial(amount):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}", f"Abnormal balance in {acct.name}"
            )
            recent = [
                t
                for (t, _l) in ctx.ledger.postings(acct.account_id)
                if t.txn_date <= ctx.period_end
            ][-8:]
            builder.transactions(recent)
            builder.calc("closing balance", "opening + debits - credits", row.closing)
            builder.calc(
                "expected side", "normal balance from chart", acct.normal_balance.value
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{acct.name} carries an abnormal {amount.format()} balance",
                narrative=(
                    f"{acct.name} normally carries a {acct.normal_balance.value} balance but "
                    f"closed the period at {row.closing.format()} in debit-positive terms."
                ),
                exposure=amount,
                packet=builder.build(),
            )


@register
class SuspiciousNewAccount(Rule):
    rule_id = "FOS-R018"
    version = "1"
    title = "New account opened with an unusual classification"
    purpose = (
        "New accounts created mid-period, especially suspense or ask-my-accountant "
        "accounts, are where unexplained amounts go to be forgotten."
    )
    category = "classification"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Clear the account to its proper classification before close and either "
        "delete it or restrict posting to it."
    )
    evidence_required = ("account", "postings")

    SUSPECT_WORDS = ("suspense", "ask my accountant", "ask accountant", "misc", "uncategor",
                     "clearing", "to be determined", "tbd", "temp")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for row in ctx.tb.rows:
            acct = row.account
            name = acct.name.lower()
            if not any(w in name for w in self.SUSPECT_WORDS):
                continue
            if row.closing.is_zero and row.movement.is_zero:
                continue
            amount = abs(row.closing) if not row.closing.is_zero else abs(row.movement)
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}", f"Activity in {acct.name}"
            )
            recent = [
                t
                for (t, _l) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
            ][:15]
            builder.transactions(recent)
            builder.calc("closing balance", "opening + period movement", row.closing)
            builder.calc("period movement", "debits - credits in period", row.movement)
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"Unresolved balance of {amount.format()} in {acct.name}",
                narrative=(
                    f"{acct.name} is a holding account and should be empty at period end. "
                    f"It closed at {row.closing.format()}."
                ),
                exposure=amount,
                packet=builder.build(),
            )


@register
class CapitalExpenseMisclassification(Rule):
    rule_id = "FOS-R019"
    version = "1"
    title = "Large equipment purchase expensed rather than capitalised"
    purpose = (
        "A single large purchase coded to repairs or materials instead of a fixed "
        "asset understates profit this year and overstates it later, and it is the "
        "most common material error in a contractor's file."
    )
    category = "classification"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Reclassify the purchase to the fixed asset register, record depreciation "
        "from the in-service date, and correct the tax treatment."
    )
    evidence_required = ("transaction", "vendor invoice", "capitalisation policy")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        threshold = ctx.policy.get("capitalisation_threshold")
        limit = (
            threshold
            if isinstance(threshold, Money)
            else Money.from_decimal("2500.00", ctx.currency)
        )
        # Job-costed materials and subcontractor lines are cost of sales by
        # definition, so they are out of scope. What is in scope is a large,
        # un-job-costed line sitting in an overhead account, which is where a
        # capital purchase actually hides.
        watch = {AccountSubtype.OPERATING_EXPENSE, AccountSubtype.EQUIPMENT_RENTAL}
        trigger = max(limit.minor_units, ctx.materiality.performance.scale(3).minor_units)
        for txn in ctx.ledger.transactions:
            if not (ctx.period_start <= txn.txn_date <= ctx.period_end):
                continue
            for line in txn.lines:
                acct = ctx.ledger.account(line.account_id)
                if acct is None or acct.subtype not in watch:
                    continue
                if line.job_id or txn.job_id:
                    continue
                if line.amount.minor_units <= trigger:
                    continue
                packet = (
                    ctx.packet(
                        f"{self.rule_id}-{line.line_id}",
                        "Assess whether the purchase should be capitalised",
                    )
                    .transaction(txn)
                    .calc("amount expensed", "line amount", line.amount)
                    .calc("capitalisation threshold", "company policy", limit)
                    .calc(
                        "performance materiality",
                        ctx.materiality.describe(),
                        ctx.materiality.performance,
                    )
                    .policy(
                        f"Purchases of durable equipment above {limit.format()} are capitalised "
                        "and depreciated over their useful life."
                    )
                    .build()
                )
                yield self.finding(
                    ctx,
                    suffix=line.line_id,
                    title=f"{line.amount.format()} expensed to {acct.name} may be capital",
                    narrative=(
                        f"A single line of {line.amount.format()} was expensed to {acct.name} on "
                        f"{txn.txn_date.isoformat()}. It exceeds both the capitalisation "
                        "threshold and performance materiality, so it needs an explicit "
                        "capital-or-expense conclusion."
                    ),
                    exposure=line.amount,
                    packet=packet,
                    confidence=Decimal("0.45"),
                )
