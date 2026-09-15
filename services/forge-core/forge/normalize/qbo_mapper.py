"""Map QuickBooks Online payloads into the Forge canonical model.

This is where the product's independence from any one ledger is actually earned.
Everything downstream, all fifty-five controls included, works on canonical
records; nothing downstream knows QuickBooks exists.

Three rules govern the mapping:

* **Never lose the original.** Every canonical record carries a
  :class:`~forge.canonical.models.Lineage` naming the raw payload and the mapping
  version, so a conclusion reached last quarter can still be explained after the
  mapping changes.
* **Refuse rather than guess.** A payload that does not balance, or an account
  whose type cannot be classified, raises. A connector that quietly coerces bad
  input produces books that tie to nothing.
* **Amounts are parsed as strings.** QuickBooks sends JSON numbers. Reading them
  as floats and rounding later reintroduces exactly the error the money type
  exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..canonical.enums import AccountSubtype, AccountType, PartyType, TxnType
from ..canonical.models import (
    Account,
    Lineage,
    Party,
    Transaction,
    TransactionLine,
)
from ..money import Money

__all__ = [
    "MAPPING_VERSION",
    "MappingError",
    "map_account",
    "map_accounts",
    "map_party",
    "map_journal_entry",
    "map_transactions",
    "QBO_TYPE_MAP",
    "QBO_SUBTYPE_MAP",
]

MAPPING_VERSION = "qbo-1"
SOURCE_SYSTEM = "quickbooks_online"


class MappingError(ValueError):
    """A payload could not be mapped without inventing something."""


# QuickBooks Classification -> Forge account type. Classification is the field
# QBO guarantees; AccountType is free-form enough that it cannot be trusted alone.
QBO_TYPE_MAP: dict[str, AccountType] = {
    "Asset": AccountType.ASSET,
    "Liability": AccountType.LIABILITY,
    "Equity": AccountType.EQUITY,
    "Revenue": AccountType.REVENUE,
    "Expense": AccountType.EXPENSE,
}

# QuickBooks AccountSubType -> Forge subtype. Anything unmapped falls back to a
# generic subtype of the right class rather than being dropped.
QBO_SUBTYPE_MAP: dict[str, AccountSubtype] = {
    # Assets
    "Checking": AccountSubtype.BANK,
    "Savings": AccountSubtype.BANK,
    "MoneyMarket": AccountSubtype.BANK,
    "CashOnHand": AccountSubtype.BANK,
    "AccountsReceivable": AccountSubtype.ACCOUNTS_RECEIVABLE,
    "UndepositedFunds": AccountSubtype.UNDEPOSITED_FUNDS,
    "Inventory": AccountSubtype.INVENTORY,
    "PrepaidExpenses": AccountSubtype.PREPAID_EXPENSE,
    "OtherCurrentAssets": AccountSubtype.OTHER_CURRENT_ASSET,
    "FurnitureAndFixtures": AccountSubtype.FIXED_ASSET,
    "MachineryAndEquipment": AccountSubtype.FIXED_ASSET,
    "Vehicles": AccountSubtype.FIXED_ASSET,
    "Buildings": AccountSubtype.FIXED_ASSET,
    "Land": AccountSubtype.FIXED_ASSET,
    "AccumulatedDepreciation": AccountSubtype.ACCUMULATED_DEPRECIATION,
    "OtherFixedAssets": AccountSubtype.FIXED_ASSET,
    "OtherLongTermAssets": AccountSubtype.OTHER_ASSET,
    # Liabilities
    "AccountsPayable": AccountSubtype.ACCOUNTS_PAYABLE,
    "CreditCard": AccountSubtype.CREDIT_CARD,
    "GlobalTaxPayable": AccountSubtype.SALES_TAX_PAYABLE,
    "SalesTaxPayable": AccountSubtype.SALES_TAX_PAYABLE,
    "PayrollTaxPayable": AccountSubtype.PAYROLL_LIABILITY,
    "PayrollClearing": AccountSubtype.PAYROLL_LIABILITY,
    "DeferredRevenue": AccountSubtype.DEFERRED_REVENUE,
    "OtherCurrentLiabilities": AccountSubtype.OTHER_CURRENT_LIABILITY,
    "NotesPayable": AccountSubtype.LOAN_PAYABLE,
    "LongTermDebt": AccountSubtype.LOAN_PAYABLE,
    "OtherLongTermLiabilities": AccountSubtype.OTHER_LIABILITY,
    # Equity
    "OpeningBalanceEquity": AccountSubtype.OTHER_EQUITY,
    "RetainedEarnings": AccountSubtype.RETAINED_EARNINGS,
    "CommonStock": AccountSubtype.COMMON_STOCK,
    "OwnersEquity": AccountSubtype.OWNER_DRAWS,
    "PartnersEquity": AccountSubtype.OTHER_EQUITY,
    "PaidInCapitalOrSurplus": AccountSubtype.OTHER_EQUITY,
    # Revenue
    "SalesOfProductIncome": AccountSubtype.SALES_REVENUE,
    "ServiceFeeIncome": AccountSubtype.SALES_REVENUE,
    "NonProfitIncome": AccountSubtype.SALES_REVENUE,
    "OtherPrimaryIncome": AccountSubtype.SALES_REVENUE,
    "OtherMiscellaneousIncome": AccountSubtype.OTHER_INCOME,
    "InterestEarned": AccountSubtype.OTHER_INCOME,
    # Expense
    "SuppliesMaterialsCogs": AccountSubtype.MATERIALS,
    "CostOfLabor": AccountSubtype.DIRECT_LABOUR,
    "CostOfLaborCos": AccountSubtype.DIRECT_LABOUR,
    "EquipmentRental": AccountSubtype.EQUIPMENT_RENTAL,
    "EquipmentRentalCos": AccountSubtype.EQUIPMENT_RENTAL,
    "ShippingFreightDelivery": AccountSubtype.COST_OF_GOODS_SOLD,
    "OtherCostsOfServiceCos": AccountSubtype.COST_OF_GOODS_SOLD,
    "PayrollExpenses": AccountSubtype.PAYROLL_EXPENSE,
    "AutoVehicleExpense": AccountSubtype.VEHICLE_EXPENSE,
    "Depreciation": AccountSubtype.DEPRECIATION_EXPENSE,
    "Insurance": AccountSubtype.OPERATING_EXPENSE,
    "RentOrLeaseOfBuildings": AccountSubtype.OPERATING_EXPENSE,
    "OfficeGeneralAdministrativeExpenses": AccountSubtype.OPERATING_EXPENSE,
    "LegalProfessionalFees": AccountSubtype.OPERATING_EXPENSE,
    "RepairMaintenance": AccountSubtype.OPERATING_EXPENSE,
    "AdvertisingPromotional": AccountSubtype.OPERATING_EXPENSE,
    "EntertainmentMeals": AccountSubtype.OPERATING_EXPENSE,
    "TravelMeals": AccountSubtype.OPERATING_EXPENSE,
    "Travel": AccountSubtype.OPERATING_EXPENSE,
    "Utilities": AccountSubtype.OPERATING_EXPENSE,
    "BankCharges": AccountSubtype.OPERATING_EXPENSE,
    "InterestPaid": AccountSubtype.INTEREST_EXPENSE,
    "TaxesPaid": AccountSubtype.INCOME_TAX_EXPENSE,
    "OtherMiscellaneousExpense": AccountSubtype.OTHER_EXPENSE,
    "OtherMiscellaneousServiceCost": AccountSubtype.OTHER_EXPENSE,
}

FALLBACK_SUBTYPE: dict[AccountType, AccountSubtype] = {
    AccountType.ASSET: AccountSubtype.OTHER_CURRENT_ASSET,
    AccountType.LIABILITY: AccountSubtype.OTHER_CURRENT_LIABILITY,
    AccountType.EQUITY: AccountSubtype.OTHER_EQUITY,
    AccountType.REVENUE: AccountSubtype.OTHER_INCOME,
    AccountType.EXPENSE: AccountSubtype.OTHER_EXPENSE,
}

CONTROL_SUBTYPES = {
    AccountSubtype.BANK,
    AccountSubtype.ACCOUNTS_RECEIVABLE,
    AccountSubtype.ACCOUNTS_PAYABLE,
    AccountSubtype.CREDIT_CARD,
}

# QuickBooks transaction entity -> Forge transaction type.
TXN_TYPE_MAP: dict[str, TxnType] = {
    "JournalEntry": TxnType.JOURNAL_ENTRY,
    "Invoice": TxnType.INVOICE,
    "CreditMemo": TxnType.CREDIT_MEMO,
    "Payment": TxnType.PAYMENT_RECEIVED,
    "SalesReceipt": TxnType.SALES_RECEIPT,
    "Bill": TxnType.BILL,
    "VendorCredit": TxnType.VENDOR_CREDIT,
    "BillPayment": TxnType.BILL_PAYMENT,
    "Purchase": TxnType.EXPENSE,
    "Deposit": TxnType.DEPOSIT,
    "Transfer": TxnType.TRANSFER,
}


def _money(value: Any, currency: str) -> Money:
    """Parse a QuickBooks amount without ever touching a float."""
    if value is None:
        return Money.zero(currency)
    if isinstance(value, Money):
        return value
    if isinstance(value, float):
        # QBO's JSON numbers arrive as floats from json.loads. Round-tripping
        # through repr preserves the shortest exact decimal representation,
        # which for a two-decimal currency amount is the amount itself.
        return Money.from_decimal(Decimal(repr(value)), currency)
    return Money.from_decimal(Decimal(str(value)), currency)


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise MappingError("transaction has no TxnDate")
    return date.fromisoformat(str(value)[:10])


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _lineage(entity: str, payload: Mapping[str, Any], raw_ref: str | None = None) -> Lineage:
    meta = payload.get("MetaData", {}) or {}
    source_id = str(payload.get("Id", ""))
    return Lineage(
        source_system=SOURCE_SYSTEM,
        source_id=f"{entity}:{source_id}",
        raw_ref=raw_ref or f"vault://{SOURCE_SYSTEM}/{entity}/{source_id}",
        mapping_version=MAPPING_VERSION,
        synced_at=_parse_datetime(meta.get("LastUpdatedTime")),
    )


def map_account(payload: Mapping[str, Any], entity_id: str, currency: str = "CAD") -> Account:
    """Map one QuickBooks ``Account``.

    Classification drives the Forge type because it is the field QBO guarantees.
    An unrecognised subtype degrades to a generic subtype of the right class,
    which keeps the statements correct while flagging nothing falsely; an
    unrecognised *classification* raises, because guessing it would put the
    balance on the wrong side of the balance sheet.
    """
    classification = payload.get("Classification")
    if classification not in QBO_TYPE_MAP:
        raise MappingError(
            f"account {payload.get('Id')} has classification {classification!r}, "
            "which cannot be mapped to a financial statement section"
        )
    account_type = QBO_TYPE_MAP[classification]
    subtype = QBO_SUBTYPE_MAP.get(
        payload.get("AccountSubType", ""), FALLBACK_SUBTYPE[account_type]
    )
    account_id = str(payload["Id"])
    return Account(
        account_id=account_id,
        entity_id=entity_id,
        number=str(payload.get("AcctNum") or account_id),
        name=payload.get("Name", "") or account_id,
        type=account_type,
        subtype=subtype,
        currency=(payload.get("CurrencyRef") or {}).get("value", currency),
        parent_id=(payload.get("ParentRef") or {}).get("value"),
        active=bool(payload.get("Active", True)),
        is_control_account=subtype in CONTROL_SUBTYPES,
        lineage=_lineage("Account", payload),
    )


def map_accounts(
    payloads: Iterable[Mapping[str, Any]], entity_id: str, currency: str = "CAD"
) -> dict[str, Account]:
    return {a.account_id: a for a in (map_account(p, entity_id, currency) for p in payloads)}


def map_party(
    payload: Mapping[str, Any], entity_id: str, party_type: PartyType
) -> Party:
    party_id = str(payload["Id"])
    # Payment terms live on QuickBooks' Term entity, referenced by id rather than
    # carried inline. Until that entity is synced there is nothing better than the
    # 30-day default, and inventing a number per customer would quietly change
    # every aging bucket.
    return Party(
        party_id=party_id,
        entity_id=entity_id,
        name=payload.get("DisplayName") or payload.get("CompanyName") or party_id,
        type=party_type,
        email=((payload.get("PrimaryEmailAddr") or {}).get("Address")),
        phone=((payload.get("PrimaryPhone") or {}).get("FreeFormNumber")),
        active=bool(payload.get("Active", True)),
        payment_terms_days=30,
        lineage=_lineage(
            "Customer" if party_type is PartyType.CUSTOMER else "Vendor", payload
        ),
    )


def map_journal_entry(
    payload: Mapping[str, Any], entity_id: str, currency: str = "CAD"
) -> Transaction:
    """Map a QuickBooks ``JournalEntry`` into a balanced canonical transaction.

    QuickBooks carries the side on each line as ``PostingType`` and the amount as
    an unsigned figure. Forge uses one signed, debit-positive amount, so the sign
    is applied here, once, rather than in every consumer.
    """
    txn_id = str(payload["Id"])
    txn_currency = (payload.get("CurrencyRef") or {}).get("value", currency)
    lines: list[TransactionLine] = []

    for index, raw_line in enumerate(payload.get("Line", []) or []):
        if raw_line.get("DetailType") != "JournalEntryLineDetail":
            continue
        detail = raw_line.get("JournalEntryLineDetail", {}) or {}
        account_ref = detail.get("AccountRef") or {}
        account_id = account_ref.get("value")
        if not account_id:
            raise MappingError(
                f"journal entry {txn_id} line {index} has no account reference"
            )
        posting = detail.get("PostingType")
        if posting not in ("Debit", "Credit"):
            raise MappingError(
                f"journal entry {txn_id} line {index} has posting type {posting!r}"
            )
        amount = _money(raw_line.get("Amount"), txn_currency)
        signed = amount if posting == "Debit" else -amount
        entity_ref = ((detail.get("Entity") or {}).get("EntityRef") or {})
        lines.append(
            TransactionLine(
                line_id=f"{txn_id}:{raw_line.get('Id', index)}",
                account_id=str(account_id),
                amount=signed,
                memo=raw_line.get("Description"),
                party_id=str(entity_ref["value"]) if entity_ref.get("value") else None,
                job_id=(detail.get("ClassRef") or {}).get("value"),
                line_number=index,
            )
        )

    if not lines:
        raise MappingError(f"journal entry {txn_id} produced no postable lines")

    meta = payload.get("MetaData", {}) or {}
    transaction = Transaction(
        txn_id=txn_id,
        entity_id=entity_id,
        type=TxnType.JOURNAL_ENTRY,
        txn_date=_parse_date(payload.get("TxnDate")),
        lines=tuple(lines),
        currency=txn_currency,
        doc_number=payload.get("DocNumber"),
        memo=payload.get("PrivateNote"),
        created_at=_parse_datetime(meta.get("CreateTime")),
        last_modified_at=_parse_datetime(meta.get("LastUpdatedTime")),
        created_by=(meta.get("LastModifiedByRef") or {}).get("name"),
        is_manual=True,
        is_adjusting=bool(payload.get("Adjustment")),
        lineage=_lineage("JournalEntry", payload),
        attributes={"sync_token": payload.get("SyncToken")},
    )
    if not transaction.is_balanced:
        raise MappingError(
            f"journal entry {txn_id} does not balance: debits less credits is "
            f"{transaction.imbalance.format()}. QuickBooks should not be able to "
            "produce this, so the payload is incomplete rather than wrong."
        )
    return transaction


def map_transactions(
    payloads_by_entity: Mapping[str, Sequence[Mapping[str, Any]]],
    entity_id: str,
    currency: str = "CAD",
) -> tuple[list[Transaction], list[str]]:
    """Map every supported transaction entity, collecting failures rather than
    raising on the first one.

    A single unmappable document must not stop a sync. The caller decides
    whether the failure list is tolerable, and the deterministic tie-out against
    QuickBooks' own trial balance will catch any material omission regardless.
    """
    transactions: list[Transaction] = []
    failures: list[str] = []
    for entity, payloads in payloads_by_entity.items():
        if entity == "JournalEntry":
            for payload in payloads:
                try:
                    transactions.append(map_journal_entry(payload, entity_id, currency))
                except MappingError as exc:
                    failures.append(str(exc))
        elif entity in TXN_TYPE_MAP:
            failures.append(
                f"{entity} mapping is not implemented yet; "
                f"{len(payloads)} document(s) were not normalised"
            )
        else:
            failures.append(f"unknown entity {entity!r}")
    return transactions, failures
