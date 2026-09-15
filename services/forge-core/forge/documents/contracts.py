"""Structured contracts for the document pipeline.

The legacy pipeline's extraction schema is preserved in substance, including
the two details that took real production pain to learn: credit-card refund
sections are identified by statement *section*, not by description text, and
per-cardholder sections must be tracked so each charge is attributed to the
right person. Both survive here as fields a model must populate and code then
checks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ExtractedTransaction",
    "LineItem",
    "ExtractedDocument",
    "VendorResearch",
    "ProposedLine",
    "ProposedEntry",
    "AccountingProposal",
    "DocumentType",
    "TaxTreatment",
]

DocumentType = Literal[
    "bank_statement", "credit_card_statement", "invoice", "receipt", "payroll", "insurance",
    "loan", "equipment_purchase", "subcontractor", "utility_bill", "lease", "contract",
    "tax_notice", "correspondence", "reference", "other",
]

TaxTreatment = Literal["taxable", "exempt", "zero_rated", "meals_50", "no_tax_component", "unknown"]


class ExtractedTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(default=None, description="YYYY-MM-DD")
    description: str = ""
    raw_vendor_name: str = ""
    debit: str | None = Field(default=None, description="Decimal string, money out or charge")
    credit: str | None = Field(default=None, description="Decimal string, money in or payment/refund")
    balance: str | None = None
    transaction_type: Literal["debit", "credit", "fee", "transfer", "payroll", "interest", "refund", "unknown"] = "unknown"
    cardholder: str = Field(default="", description="Cardholder section name on a card statement, else empty")
    page: int = 1


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: str | None = None
    unit_price: str | None = None
    amount: str
    tax: str | None = None


class ExtractedDocument(BaseModel):
    """Pass 1 output. Everything here is DATA extracted from an untrusted file."""

    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType = "other"
    document_date: str | None = None
    vendor_or_issuer: str = ""
    total_amount: str | None = None
    subtotal: str | None = None
    tax_amount: str | None = None
    tax_number: str = ""
    payment_method: Literal["cash", "cheque", "credit_card", "e-transfer", "bank_transfer", "unknown"] = "unknown"
    currency: str = "CAD"
    summary: str = Field(default="", max_length=300)
    transactions: list[ExtractedTransaction] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    closing_balance: str | None = None
    confidence: int = Field(default=0, ge=0, le=100)
    notes: str = ""

    @property
    def is_statement(self) -> bool:
        return self.document_type in ("bank_statement", "credit_card_statement")


class VendorResearch(BaseModel):
    """Pass 2 output for a vendor the deterministic table did not recognise."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str = ""
    vendor_type: str = ""
    industry: str = ""
    typical_category: str = "other"
    tax_applicable: bool = True
    input_tax_credit_claimable: bool = True
    credit_notes: str = ""
    cra_notes: str = ""
    confidence: int = Field(default=0, ge=0, le=100)
    personal_use_risk: bool = False
    personal_use_note: str = ""


class ProposedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_number: str
    account_name: str
    side: Literal["debit", "credit"]
    amount: str = Field(description="Decimal string, NET of sales tax; tax lines are added by code")
    memo: str = ""


class ProposedEntry(BaseModel):
    """One journal entry a model proposes. Tax is never on these lines.

    The model states the *treatment*; the engine computes the tax split. That
    is the accounting version of "AI classifies, code calculates".
    """

    model_config = ConfigDict(extra="forbid")

    date: str
    description: str
    lines: list[ProposedLine]
    tax_treatment: TaxTreatment = "unknown"
    gross_amount: str | None = Field(default=None, description="Tax-inclusive total from the document, if known")
    source_transaction_index: int | None = None
    vendor: str = ""
    confidence: int = Field(default=0, ge=0, le=100)
    reasoning: str = Field(default="", max_length=600)
    needs_client_clarification: bool = False
    clarification_question: str = ""


class AccountingProposal(BaseModel):
    """Pass 3 output."""

    model_config = ConfigDict(extra="forbid")

    entries: list[ProposedEntry] = Field(default_factory=list)
    questions_for_client: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
    notes: str = ""
