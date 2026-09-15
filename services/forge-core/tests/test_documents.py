"""The document pipeline: deterministic tax, vendor table, salvage, and the
whole three-pass flow under the offline provider."""

from __future__ import annotations

import json
from decimal import Decimal

from forge.agents.gateway import ModelGateway
from forge.agents.providers import ModelResponse, OfflineDeterministicProvider, Provider
from forge.clients import ClientProfile
from forge.documents import (
    KNOWN_VENDORS,
    VendorCache,
    match_known_vendor,
    process_document,
    salvage_json,
    tax_split,
)
from forge.documents.vendors import normalise_vendor
from forge.money import Money


class TestTaxSplit:
    def test_taxable_splits_exactly(self):
        s = tax_split(Money.from_decimal("113.00"), Decimal("0.13"), "taxable")
        assert (s.net, s.tax, s.recoverable_tax) == (
            Money.from_decimal("100.00"), Money.from_decimal("13.00"), Money.from_decimal("13.00"))
        assert s.net + s.tax == s.gross

    def test_meals_recover_half_the_tax(self):
        s = tax_split(Money.from_decimal("113.00"), Decimal("0.13"), "meals_50")
        assert s.recoverable_tax == Money.from_decimal("6.50")
        assert s.net == Money.from_decimal("106.50")
        assert s.net + s.recoverable_tax == s.gross

    def test_exempt_carries_no_tax(self):
        s = tax_split(Money.from_decimal("500.00"), Decimal("0.13"), "exempt")
        assert s.tax.is_zero and s.net == s.gross

    def test_quebec_rate_splits_to_the_cent(self):
        s = tax_split(Money.from_decimal("1149.75"), Decimal("0.14975"), "taxable")
        assert s.net == Money.from_decimal("1000.00")


class TestVendorTable:
    def test_common_canadian_vendors_are_recognised(self):
        assert match_known_vendor("PETRO-CANADA #4421").category == "fuel"
        assert match_known_vendor("TIM HORTONS 2201").tax_treatment == "meals_50"
        assert match_known_vendor("INTACT INSURANCE PREMIUM").tax_treatment == "exempt"
        assert match_known_vendor("CANADA REVENUE AGENCY").tax_treatment == "no_tax_component"
        assert match_known_vendor("MONTHLY SERVICE FEE").category == "bank_charges"

    def test_personal_use_risk_is_flagged_not_asserted(self):
        h = match_known_vendor("LOBLAWS 1044")
        assert h.personal_use_risk and h.account_number == "1999"

    def test_unknown_vendor_returns_none(self):
        assert match_known_vendor("ZQX HOLDINGS 9") is None

    def test_every_pattern_compiles_and_maps_to_a_treatment(self):
        import re
        for pattern, hint in KNOWN_VENDORS:
            re.compile(pattern)
            assert hint.tax_treatment in ("taxable", "exempt", "zero_rated", "meals_50", "no_tax_component", "unknown")

    def test_normalisation_strips_bank_noise(self):
        assert normalise_vendor("POS PURCHASE PETRO-CAN 00412 TORONTO") == "petro can toronto"


class TestVendorCache:
    def test_operator_correction_outranks_the_table(self, tmp_path):
        cache = VendorCache.load("C1", tmp_path)
        cache.correct("AMAZON.CA", account_number="5100", account_name="Materials", tax_treatment="taxable")
        cache.save()
        again = VendorCache.load("C1", tmp_path)
        # Keys are normalised bank text, so the same merchant with a store
        # number and card noise resolves to the same precedent.
        hint = again.get("POS PURCHASE AMAZON.CA 00913")
        assert hint is not None and hint.source == "operator" and hint.confidence == 100
        assert list(again.precedents()) == ["amazon ca"]


class TestSalvage:
    def test_truncated_array_is_recovered_and_marked(self):
        text = '{"transactions":[{"a":1},{"a":2},{"a":3},{"a":4, "descr'
        parsed = salvage_json(text)
        assert parsed is not None and parsed["_salvaged"] is True
        assert len(parsed["transactions"]) == 3

    def test_valid_json_passes_straight_through(self):
        assert salvage_json('prefix {"x": 1}') == {"x": 1}

    def test_hopeless_input_returns_none(self):
        assert salvage_json("no json here") is None


class ScriptedDocs(Provider):
    """Replays a realistic extraction and proposal so the whole flow runs."""

    kind = "openai_compatible"

    def complete(self, *, spec, system, user, schema=None, images=(), max_tokens=4096, web_search=False):
        title = (schema or {}).get("title", "")
        if "ExtractedDocument" in title:
            payload = {
                "document_type": "credit_card_statement", "document_date": "2026-06-30",
                "vendor_or_issuer": "Meridian Visa", "total_amount": "1290.85", "subtotal": None,
                "tax_amount": None, "tax_number": "", "payment_method": "credit_card", "currency": "CAD",
                "summary": "June card statement", "closing_balance": "1290.85", "confidence": 92, "notes": "",
                "line_items": [],
                "transactions": [
                    {"date": "2026-06-03", "description": "PETRO-CANADA 4421", "raw_vendor_name": "PETRO-CANADA",
                     "debit": "113.00", "credit": None, "balance": None, "transaction_type": "debit", "cardholder": "ROBERT", "page": 1},
                    {"date": "2026-06-09", "description": "TIM HORTONS 2201", "raw_vendor_name": "TIM HORTONS",
                     "debit": "22.60", "credit": None, "balance": None, "transaction_type": "debit", "cardholder": "ROBERT", "page": 1},
                    {"date": "2026-06-12", "description": "AMAZON TRAVEL CREDIT", "raw_vendor_name": "AMAZON",
                     "debit": "-50.00", "credit": None, "balance": None, "transaction_type": "debit", "cardholder": "", "page": 2},
                ],
            }
        elif "VendorResearch" in title:
            payload = {"vendor_name": "Unknown", "vendor_type": "retail", "industry": "", "typical_category": "office_supplies",
                       "tax_applicable": True, "input_tax_credit_claimable": True, "credit_notes": "", "cra_notes": "",
                       "confidence": 70, "personal_use_risk": False, "personal_use_note": ""}
        elif "AccountingProposal" in title:
            payload = {"entries": [
                {"date": "2026-06-03", "description": "Fuel", "tax_treatment": "taxable", "gross_amount": "113.00",
                 "vendor": "PETRO-CANADA", "confidence": 90, "reasoning": "Fuel for fleet", "source_transaction_index": 0,
                 "needs_client_clarification": False, "clarification_question": "",
                 "lines": [{"account_number": "5400", "account_name": "Fuel", "side": "debit", "amount": "100.00", "memo": ""},
                           {"account_number": "2100", "account_name": "Visa", "side": "credit", "amount": "113.00", "memo": ""}]},
                {"date": "2026-06-09", "description": "Coffee meeting", "tax_treatment": "meals_50", "gross_amount": "22.60",
                 "vendor": "TIM HORTONS", "confidence": 80, "reasoning": "", "source_transaction_index": 1,
                 "needs_client_clarification": False, "clarification_question": "",
                 "lines": [{"account_number": "7300", "account_name": "Meals", "side": "debit", "amount": "21.30", "memo": ""},
                           {"account_number": "2100", "account_name": "Visa", "side": "credit", "amount": "22.60", "memo": ""}]},
                {"date": "2026-06-20", "description": "Broken", "tax_treatment": "exempt", "gross_amount": "10.00",
                 "vendor": "", "confidence": 50, "reasoning": "", "source_transaction_index": None,
                 "needs_client_clarification": False, "clarification_question": "",
                 "lines": [{"account_number": "6400", "account_name": "Office", "side": "debit", "amount": "10.00", "memo": ""},
                           {"account_number": "2100", "account_name": "Visa", "side": "credit", "amount": "9.00", "memo": ""}]},
            ], "questions_for_client": ["Was the Amazon credit a refund of a business purchase?"], "confidence": 85, "notes": ""}
        else:
            return OfflineDeterministicProvider().complete(spec=spec, system=system, user=user, schema=schema)
        text = json.dumps(payload)
        return ModelResponse(text=text, input_tokens=100, output_tokens=len(text) // 4, model_used=spec.model)


def _gateway():
    from forge.agents.catalogue import ModelSpec
    spec = ModelSpec("anthropic", "scripted", "claude", "anthropic", Decimal(1), Decimal(1), 3, supports_vision=True, supports_web_search=True)
    return ModelGateway(providers={"anthropic": ScriptedDocs(), "offline": OfflineDeterministicProvider()}, models=(spec,))


class TestPipeline:
    def _profile(self):
        return ClientProfile(client_id="C1", business_name="Eternal", province="ON", naics_code="561730",
                             annual_revenue_estimate=Money.from_decimal("1800000.00"))

    def test_three_passes_produce_balanced_tax_split_entries(self, tmp_path):
        result = process_document(b"%PDF-1.4 fake", "application/pdf", profile=self._profile(),
                                  gateway=_gateway(), vendor_cache=VendorCache.load("C1", tmp_path))
        assert result.extracted is not None and result.extracted.document_type == "credit_card_statement"
        assert len(result.prepared_entries) == 2
        fuel = result.prepared_entries[0]
        assert any(ln["account_number"] == "2200" and ln["amount"] == "13.00" for ln in fuel["lines"])
        debits = sum(Decimal(ln["amount"]) for ln in fuel["lines"] if ln["side"] == "debit")
        credits = sum(Decimal(ln["amount"]) for ln in fuel["lines"] if ln["side"] == "credit")
        assert debits == credits == Decimal("113.00")

    def test_meals_get_half_the_credit_and_still_balance(self, tmp_path):
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=_gateway())
        meals = result.prepared_entries[1]
        tax_line = next(ln for ln in meals["lines"] if ln["account_number"] == "2200")
        assert tax_line["amount"] == "1.30"
        debits = sum(Decimal(ln["amount"]) for ln in meals["lines"] if ln["side"] == "debit")
        credits = sum(Decimal(ln["amount"]) for ln in meals["lines"] if ln["side"] == "credit")
        assert debits == credits

    def test_unbalanced_proposal_is_rejected_with_a_reason(self):
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=_gateway())
        assert len(result.rejected_entries) == 1
        assert "does not balance" in result.rejected_entries[0][1]

    def test_negative_card_amount_is_reclassified_as_refund(self):
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=_gateway())
        assert result.extracted.transactions[2].transaction_type == "refund"
        assert any("refund" in w for w in result.warnings)

    def test_known_vendors_skip_research_and_unknown_ones_are_cached(self, tmp_path):
        cache = VendorCache.load("C1", tmp_path)
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=_gateway(), vendor_cache=cache)
        assert result.vendor_hints["PETRO-CANADA"].source == "known_vendor_table"
        assert result.vendor_hints["Meridian Visa"].source == "research"
        assert cache.get("Meridian Visa") is not None

    def test_document_text_is_fenced_as_untrusted(self):
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=_gateway())
        assert result.packet is not None
        assert result.packet.untrusted
        assert all(u.origin == "ocr_document" for u in result.packet.untrusted)

    def test_every_model_call_is_accounted(self):
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=_gateway())
        roles = {c.role.value for c in result.calls}
        assert {"bookkeeper", "ap_specialist", "close_accountant"} <= roles
        assert result.cost_micros > 0

    def test_offline_provider_runs_the_pipeline_without_a_key(self):
        result = process_document(b"x", "image/png", profile=self._profile(), gateway=ModelGateway.offline())
        assert result.extracted is not None
        assert result.prepared_entries == []
