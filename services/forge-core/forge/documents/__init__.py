"""Documents: extraction, vendor identification and proposed entries, as evidence."""

from .contracts import AccountingProposal, ExtractedDocument, ProposedEntry, VendorResearch
from .pipeline import DocumentResult, process_document, salvage_json, tax_split
from .vendors import KNOWN_VENDORS, VendorCache, VendorHint, match_known_vendor

__all__ = [
    "process_document", "DocumentResult", "tax_split", "salvage_json",
    "ExtractedDocument", "VendorResearch", "AccountingProposal", "ProposedEntry",
    "VendorCache", "VendorHint", "KNOWN_VENDORS", "match_known_vendor",
]
