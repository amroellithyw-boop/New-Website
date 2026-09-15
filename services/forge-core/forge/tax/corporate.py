"""Corporate income tax provision estimate for a CCPC.

An estimate for planning and for the year-end file, not a return. Applies the
small-business rate to active business income up to the limit and the general
rate above it, after the add-backs that are computable from the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..canonical.enums import AccountSubtype
from ..clients.tax_regions import ONTARIO_CCPC, CorporateRates
from ..money import Money, msum
from ..rules.base import RuleContext

__all__ = ["TaxProvision", "corporate_tax_provision"]


@dataclass(frozen=True)
class TaxProvision:
    accounting_income: Money
    add_back_meals: Money
    add_back_depreciation: Money
    taxable_income_estimate: Money
    small_business_portion: Money
    general_portion: Money
    tax_small_business: Money
    tax_general: Money
    rates: CorporateRates
    notes: tuple[str, ...] = ()

    @property
    def total_tax(self) -> Money:
        return self.tax_small_business + self.tax_general

    @property
    def effective_rate(self) -> Decimal | None:
        return self.total_tax.ratio_to(self.taxable_income_estimate)

    def to_dict(self) -> dict:
        return {k: (str(v.to_decimal()) if isinstance(v, Money) else v) for k, v in {
            "accounting_income": self.accounting_income, "add_back_meals": self.add_back_meals,
            "add_back_depreciation": self.add_back_depreciation,
            "taxable_income_estimate": self.taxable_income_estimate,
            "tax_small_business": self.tax_small_business, "tax_general": self.tax_general,
            "total_tax": self.total_tax, "notes": list(self.notes),
        }.items()}


def corporate_tax_provision(ctx: RuleContext, *, rates: CorporateRates = ONTARIO_CCPC, cca_claim: Money | None = None) -> TaxProvision:
    cur = ctx.currency
    income = ctx.pl_ytd.net_income
    meals = Money.zero(cur)
    for row in ctx.tb_ytd.rows:
        if "meal" in row.account.name.lower() or "entertainment" in row.account.name.lower():
            meals = meals + row.presentation_movement
    meals_add = meals.allocate([1, 1])[0]
    dep = msum((r.presentation_movement for r in ctx.tb_ytd.rows_of(subtype=AccountSubtype.DEPRECIATION_EXPENSE)), cur)
    notes = ["Fiscal year to date; annualise before comparing with instalments.",
             "Book depreciation is added back; capital cost allowance is deducted only when a schedule is supplied."]
    taxable = income + meals_add + dep - (cca_claim or Money.zero(cur))
    if cca_claim is None:
        notes.append("No CCA claim supplied: taxable income is overstated by the allowable claim.")
    limit = Money.from_decimal(rates.small_business_limit, cur)
    if taxable.minor_units <= 0:
        sb = gen = Money.zero(cur)
        notes.append("No taxable income estimated; a loss carries forward.")
    else:
        sb = taxable if taxable <= limit else limit
        gen = taxable - sb
    return TaxProvision(
        accounting_income=income, add_back_meals=meals_add, add_back_depreciation=dep,
        taxable_income_estimate=taxable, small_business_portion=sb, general_portion=gen,
        tax_small_business=sb.scale(rates.combined_small_business), tax_general=gen.scale(rates.combined_general),
        rates=rates, notes=tuple(notes),
    )
