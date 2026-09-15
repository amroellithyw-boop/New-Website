"""Routing, independence, cascade, fallback and budget in the model gateway."""

from __future__ import annotations

from decimal import Decimal

import pytest

from forge.agents.catalogue import CATALOGUE, ModelSpec, available_models
from forge.agents.contracts import PreparerProposal
from forge.agents.gateway import Budget, BudgetExceeded, GatewayError, ModelGateway, RoutingPolicy
from forge.agents.providers import (
    OfflineDeterministicProvider,
    Provider,
    ProviderError,
    ProviderRefused,
    _strict,
)
from forge.canonical.enums import AgentRole, RiskTier


class Scripted(Provider):
    """A provider that replays canned behaviour so routing can be tested."""

    kind = "openai_compatible"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    def complete(self, *, spec, system, user, schema=None, images=(), max_tokens=4096, web_search=False):
        self.calls += 1
        action = self.behaviour
        if isinstance(action, Exception):
            raise action
        offline = OfflineDeterministicProvider()
        response = offline.complete(spec=spec, system=system, user=user, schema=schema)
        if isinstance(action, dict):
            import json
            payload = json.loads(response.text)
            payload.update(action)
            response.text = json.dumps(payload)
        response.model_used = spec.model
        return response


def _spec(provider, model, family, quality, cost="1.0", **kw):
    return ModelSpec(provider, model, family, "openai_compatible", Decimal(cost), Decimal(cost), quality, **kw)


def _gateway(specs, providers, **kw):
    providers = {**providers, "offline": OfflineDeterministicProvider()}
    return ModelGateway(providers=providers, models=tuple(specs) + (CATALOGUE[0],), **kw)


class TestCatalogue:
    def test_offline_is_always_available(self):
        assert [m.key for m in available_models({})] == ["offline:deterministic"]

    def test_keys_unlock_providers(self):
        keys = {m.provider for m in available_models({"ANTHROPIC_API_KEY": "a", "GROQ_API_KEY": "g"})}
        assert keys == {"offline", "anthropic", "groq"}

    def test_every_catalogue_entry_has_a_provider_env(self):
        from forge.agents.catalogue import PROVIDER_ENV
        for spec in CATALOGUE:
            assert spec.provider in PROVIDER_ENV, spec.key


class TestRouting:
    def test_cheapest_capable_model_wins(self):
        cheap = _spec("a", "small", "fam-a", 3, "0.1")
        dear = _spec("b", "big", "fam-b", 4, "5.0")
        gw = _gateway([cheap, dear], {"a": Scripted({}), "b": Scripted({})})
        assert gw.select(tier=RiskTier.R2, role=AgentRole.BOOKKEEPER).key == "a:small"

    def test_high_tier_refuses_a_model_below_the_quality_bar(self):
        cheap = _spec("a", "small", "fam-a", 2, "0.1")
        dear = _spec("b", "big", "fam-b", 4, "5.0")
        gw = _gateway([cheap, dear], {"a": Scripted({}), "b": Scripted({})})
        assert gw.select(tier=RiskTier.R4, role=AgentRole.CFO).key == "b:big"

    def test_adversary_always_gets_a_strong_model(self):
        cheap = _spec("a", "small", "fam-a", 3, "0.1")
        dear = _spec("b", "big", "fam-b", 4, "5.0")
        gw = _gateway([cheap, dear], {"a": Scripted({}), "b": Scripted({})})
        assert gw.select(tier=RiskTier.R1, role=AgentRole.ADVERSARY).key == "b:big"

    def test_free_local_model_is_preferred_when_capable(self):
        local = _spec("ollama", "llama", "llama", 3, "0", local=True)
        hosted = _spec("a", "small", "fam-a", 3, "0.1")
        gw = _gateway([local, hosted], {"ollama": Scripted({}), "a": Scripted({})})
        assert gw.select(tier=RiskTier.R1, role=AgentRole.BOOKKEEPER).key == "ollama:llama"

    def test_local_preference_can_be_switched_off(self):
        local = _spec("ollama", "llama", "llama", 3, "0", local=True)
        hosted = _spec("a", "small", "fam-a", 3, "0.1")
        gw = _gateway([local, hosted], {"ollama": Scripted({}), "a": Scripted({})},
                      policy=RoutingPolicy(prefer_local=False))
        # Still cheapest by price, and free beats paid.
        assert gw.select(tier=RiskTier.R1, role=AgentRole.BOOKKEEPER).key == "ollama:llama"

    def test_frontier_models_are_off_by_default(self):
        frontier = _spec("a", "huge", "fam-a", 5, "10")
        normal = _spec("a", "big", "fam-a", 4, "5")
        gw = _gateway([frontier, normal], {"a": Scripted({})})
        assert gw.select(tier=RiskTier.R4, role=AgentRole.CFO).key == "a:big"
        gw2 = _gateway([frontier, normal], {"a": Scripted({})}, policy=RoutingPolicy(allow_frontier=True))
        assert "a:huge" in {m.key for m in gw2.candidates(tier=RiskTier.R4, role=AgentRole.CFO)}

    def test_pdf_input_routes_to_a_provider_that_accepts_pdfs(self):
        a = ModelSpec("anthropic", "claude", "claude", "anthropic", Decimal(1), Decimal(1), 3, supports_vision=True)
        b = _spec("b", "vision", "fam-b", 3, "0.1", supports_vision=True)
        gw = _gateway([a, b], {"anthropic": Scripted({}), "b": Scripted({})})
        chosen = gw.select(tier=RiskTier.R2, role=AgentRole.BOOKKEEPER, need_pdf=True)
        assert chosen.provider == "anthropic"


class TestIndependence:
    def test_three_families_give_three_independent_reviewers(self):
        specs = [_spec("a", "m", "claude", 4), _spec("b", "m", "gpt", 4), _spec("c", "m", "grok", 4)]
        gw = _gateway(specs, {"a": Scripted({}), "b": Scripted({}), "c": Scripted({})})
        used = ["claude"]
        second = gw.select(tier=RiskTier.R3, role=AgentRole.CONTROLLER, avoid_families=used)
        used.append(second.family)
        third = gw.select(tier=RiskTier.R3, role=AgentRole.ADVERSARY, avoid_families=used)
        assert len({"claude", second.family, third.family}) == 3

    def test_single_family_falls_back_rather_than_failing(self):
        gw = _gateway([_spec("a", "m", "claude", 4)], {"a": Scripted({})})
        chosen = gw.select(tier=RiskTier.R3, role=AgentRole.CONTROLLER, avoid_families=["claude"])
        assert chosen.family == "claude"  # the caller records independence_unavailable

    def test_independent_family_lookup(self):
        specs = [_spec("a", "m", "claude", 4), _spec("b", "m", "gpt", 4)]
        gw = _gateway(specs, {"a": Scripted({}), "b": Scripted({})})
        assert gw.independent_family_for(["claude"]) == "gpt"
        assert gw.independent_family_for(["claude", "gpt"]) is None


def _call(gw, **kw):
    return gw.call(role=AgentRole.BOOKKEEPER, tier=RiskTier.R2, system="You are the bookkeeper",
                   user='{"evidence":[{"id":"TXN-1"}]}', response_model=PreparerProposal,
                   packet_checksum="abc", **kw)


class TestFallbackAndCascade:
    def test_retryable_provider_error_falls_through_to_the_next_model(self):
        broken = Scripted(ProviderError("timeout", retryable=True))
        fine = Scripted({})
        gw = _gateway([_spec("a", "m", "fa", 3, "0.1"), _spec("b", "m", "fb", 3, "0.2")],
                      {"a": broken, "b": fine})
        parsed, record = _call(gw)
        assert record.provider == "b"
        assert broken.calls == 1 and fine.calls == 1
        assert any(not c.succeeded for c in gw.calls)

    def test_refusal_moves_to_another_model(self):
        refusing = Scripted(ProviderRefused("policy"))
        fine = Scripted({})
        gw = _gateway([_spec("a", "m", "fa", 3, "0.1"), _spec("b", "m", "fb", 3, "0.2")],
                      {"a": refusing, "b": fine})
        _parsed, record = _call(gw)
        assert record.provider == "b"

    def test_all_models_failing_raises_with_the_reasons(self):
        gw = _gateway([_spec("a", "m", "fa", 3)], {"a": Scripted(ProviderError("down", retryable=True))})
        with pytest.raises(GatewayError, match="down"):
            _call(gw)

    def test_cascade_escalates_on_low_confidence(self):
        cheap = Scripted({"confidence": "0.3"})
        strong = Scripted({"confidence": "0.9"})
        gw = _gateway([_spec("a", "small", "fa", 2, "0.1"), _spec("b", "big", "fb", 4, "5")],
                      {"a": cheap, "b": strong})
        parsed, record = _call(gw, cascade=True)
        assert record.provider == "b"
        assert cheap.calls == 1 and strong.calls == 1
        assert parsed.confidence == Decimal("0.9")

    def test_cascade_stops_early_when_the_cheap_model_is_confident(self):
        cheap = Scripted({"confidence": "0.95"})
        strong = Scripted({"confidence": "0.95"})
        gw = _gateway([_spec("a", "small", "fa", 2, "0.1"), _spec("b", "big", "fb", 4, "5")],
                      {"a": cheap, "b": strong})
        _parsed, record = _call(gw, cascade=True)
        assert record.provider == "a"
        assert strong.calls == 0

    def test_cascade_escalates_on_missing_evidence(self):
        cheap = Scripted({"confidence": "0.9", "missing_evidence": ["the invoice"]})
        strong = Scripted({"confidence": "0.9"})
        gw = _gateway([_spec("a", "small", "fa", 2, "0.1"), _spec("b", "big", "fb", 4, "5")],
                      {"a": cheap, "b": strong})
        _parsed, record = _call(gw, cascade=True)
        assert record.provider == "b"


class TestBudget:
    def test_call_ceiling_stops_the_run_loudly(self):
        gw = _gateway([_spec("a", "m", "fa", 3)], {"a": Scripted({})}, budget=Budget(max_calls=1))
        _call(gw)
        with pytest.raises(BudgetExceeded):
            _call(gw)

    def test_cost_is_accounted_per_model(self):
        gw = _gateway([_spec("a", "m", "fa", 3, "2.0")], {"a": Scripted({})})
        _call(gw)
        summary = gw.cost_summary()
        assert summary["calls"] == 1
        assert "a:m" in summary["by_model"]
        assert summary["cost_micros"] > 0


class TestStrictSchema:
    def test_every_object_gets_additional_properties_false_and_full_required(self):
        schema = PreparerProposal.model_json_schema()
        strict = _strict(schema)

        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    assert node["additionalProperties"] is False
                    assert set(node["required"]) == set(node["properties"])
                for v in node.values():
                    check(v)
            elif isinstance(node, list):
                for v in node:
                    check(v)

        check(strict)


class TestReviewLoopIndependence:
    def test_three_families_make_an_r4_chain_independent(self, gate_passing_case):
        from forge.pipeline import run_continuous_controller

        specs = [
            _spec("a", "m", "claude", 4, "1"), _spec("b", "m", "gpt", 4, "1"),
            _spec("c", "m", "grok", 4, "1"), _spec("d", "m", "gemini", 4, "1"),
        ]
        gw = _gateway(specs, {k: Scripted({}) for k in "abcd"})
        run = run_continuous_controller(
            gate_passing_case.ledger, period_start=gate_passing_case.period_start,
            period_end=gate_passing_case.period_end,
            statements=gate_passing_case.company.statements,
            gateway=gw, review=True, review_limit=3,
        )
        reviewed = [i for i in run.work_items if i.calls]
        assert reviewed
        for item in reviewed:
            families = [c.provider for c in item.calls]
            # The first reviewers after the preparer must not reuse its family.
            assert len(set(families[:3])) == min(3, len(families))
            assert not any(e.action == "independence_unavailable" for e in item.audit[:6])
