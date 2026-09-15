"""Canonical enumerations shared by every layer of ForgeOS."""

from __future__ import annotations

from enum import Enum


class AccountType(str, Enum):
    """Top-level financial statement classification."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

    @property
    def normal_balance(self) -> Side:
        """The side on which this account type normally carries a balance."""
        return Side.DEBIT if self in (AccountType.ASSET, AccountType.EXPENSE) else Side.CREDIT

    @property
    def is_balance_sheet(self) -> bool:
        return self in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)

    @property
    def is_income_statement(self) -> bool:
        return not self.is_balance_sheet


class AccountSubtype(str, Enum):
    """Sub-classification driving statement ordering and control applicability."""

    # Assets
    BANK = "bank"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"
    UNDEPOSITED_FUNDS = "undeposited_funds"
    INVENTORY = "inventory"
    PREPAID_EXPENSE = "prepaid_expense"
    WORK_IN_PROGRESS = "work_in_progress"
    OTHER_CURRENT_ASSET = "other_current_asset"
    FIXED_ASSET = "fixed_asset"
    ACCUMULATED_DEPRECIATION = "accumulated_depreciation"
    OTHER_ASSET = "other_asset"
    # Liabilities
    ACCOUNTS_PAYABLE = "accounts_payable"
    CREDIT_CARD = "credit_card"
    SALES_TAX_PAYABLE = "sales_tax_payable"
    PAYROLL_LIABILITY = "payroll_liability"
    ACCRUED_LIABILITY = "accrued_liability"
    DEFERRED_REVENUE = "deferred_revenue"
    OTHER_CURRENT_LIABILITY = "other_current_liability"
    LOAN_PAYABLE = "loan_payable"
    OTHER_LIABILITY = "other_liability"
    # Equity
    COMMON_STOCK = "common_stock"
    RETAINED_EARNINGS = "retained_earnings"
    OWNER_DRAWS = "owner_draws"
    OWNER_CONTRIBUTION = "owner_contribution"
    OTHER_EQUITY = "other_equity"
    # Revenue
    SALES_REVENUE = "sales_revenue"
    OTHER_INCOME = "other_income"
    # Expense
    COST_OF_GOODS_SOLD = "cost_of_goods_sold"
    DIRECT_LABOUR = "direct_labour"
    SUBCONTRACTOR = "subcontractor"
    MATERIALS = "materials"
    EQUIPMENT_RENTAL = "equipment_rental"
    VEHICLE_EXPENSE = "vehicle_expense"
    PAYROLL_EXPENSE = "payroll_expense"
    OPERATING_EXPENSE = "operating_expense"
    DEPRECIATION_EXPENSE = "depreciation_expense"
    INTEREST_EXPENSE = "interest_expense"
    INCOME_TAX_EXPENSE = "income_tax_expense"
    OTHER_EXPENSE = "other_expense"


class Side(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TxnType(str, Enum):
    """Source document type for a canonical transaction."""

    JOURNAL_ENTRY = "journal_entry"
    INVOICE = "invoice"
    CREDIT_MEMO = "credit_memo"
    PAYMENT_RECEIVED = "payment_received"
    SALES_RECEIPT = "sales_receipt"
    BILL = "bill"
    VENDOR_CREDIT = "vendor_credit"
    BILL_PAYMENT = "bill_payment"
    EXPENSE = "expense"
    CHEQUE = "cheque"
    DEPOSIT = "deposit"
    TRANSFER = "transfer"
    PAYROLL = "payroll"
    INVENTORY_ADJUSTMENT = "inventory_adjustment"
    OPENING_BALANCE = "opening_balance"


class PartyType(str, Enum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    EMPLOYEE = "employee"
    CONTRACTOR = "contractor"


class Severity(str, Enum):
    """Control severity. Drives triage order and the risk score floor."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


class RiskTier(str, Enum):
    """Risk tier from the product constitution section 4.3."""

    R0 = "R0"  # deterministic, no judgment
    R1 = "R1"  # routine, reversible
    R2 = "R2"  # moderate judgment
    R3 = "R3"  # material / specialist
    R4 = "R4"  # strategic / irreversible / regulated

    @property
    def level(self) -> int:
        return int(self.value[1])


class WorkItemState(str, Enum):
    """States in the work-item state machine (FOS-007)."""

    DRAFT = "draft"
    VALIDATING = "validating"
    VALIDATION_FAILED = "validation_failed"
    PREPARING = "preparing"
    AWAITING_REVIEW = "awaiting_review"
    RETURNED_FOR_CORRECTION = "returned_for_correction"
    CORRECTING = "correcting"
    ESCALATED = "escalated"
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTION_PENDING = "action_pending"
    ACTION_EXECUTED = "action_executed"
    ACTION_VERIFIED = "action_verified"
    ACTION_FAILED = "action_failed"
    CLOSED = "closed"

    @property
    def is_terminal(self) -> bool:
        return self in (WorkItemState.CLOSED, WorkItemState.REJECTED)


class ReviewDecisionKind(str, Enum):
    APPROVE = "approve"
    RETURN = "return"
    ESCALATE = "escalate"


class AgentRole(str, Enum):
    """The finance organization modelled in software (blueprint section 3)."""

    BOOKKEEPER = "bookkeeper"
    AP_SPECIALIST = "ap_specialist"
    AR_SPECIALIST = "ar_specialist"
    PAYROLL_SPECIALIST = "payroll_specialist"
    CLOSE_ACCOUNTANT = "close_accountant"
    ACCOUNTING_MANAGER = "accounting_manager"
    CONTROLLER = "controller"
    TAX_SPECIALIST = "tax_specialist"
    TREASURY_SPECIALIST = "treasury_specialist"
    FPA_ANALYST = "fpa_analyst"
    FPA_MANAGER = "fpa_manager"
    COMMERCIAL_ANALYST = "commercial_analyst"
    FINANCE_BUSINESS_PARTNER = "finance_business_partner"
    VP_FINANCE = "vp_finance"
    CFO = "cfo"
    ADVERSARY = "adversary"
    POLICY_REVIEWER = "policy_reviewer"
    EVIDENCE_AUDITOR = "evidence_auditor"
    ORCHESTRATOR = "orchestrator"
    HUMAN = "human"


class AutonomyLevel(str, Enum):
    """Staged autonomy (blueprint section 12). Never skip a level."""

    A0_OBSERVE = "A0"
    A1_DRAFT = "A1"
    A2_APPROVE_TO_EXECUTE = "A2"
    A3_BOUNDED_AUTONOMY = "A3"
    A4_STRATEGIC = "A4"

    @property
    def level(self) -> int:
        return int(self.value[1])
