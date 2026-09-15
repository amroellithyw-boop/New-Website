"""Provider-agnostic model gateway.

Constitution rule #11: ForgeOS owns the orchestration, evidence, tests and
business logic. Model providers are replaceable components. Nothing above this
module imports a vendor SDK or knows a model name.

The gateway also carries three things that are easy to forget and expensive to
retrofit:

* **Routing by risk.** Cheap models do extraction and routine first-pass work;
  stronger models are spent where materiality, ambiguity or disagreement makes
  the extra reasoning change the outcome.
* **Reviewer independence.** For material work the reviewer should not run on
  the same model family as the preparer, because two instances of one model fail
  the same way and a correlated failure looks exactly like agreement.
* **Cost telemetry.** Every call records tokens, latency and cost against a work
  item, so "what did this close cost" is a query and not a guess.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..canonical.enums import AgentRole, RiskTier
from .contracts import AgentCall

__all__ = [
    "ModelSpec",
    "ModelResponse",
    "Provider",
    "OfflineDeterministicProvider",
    "AnthropicProvider",
    "ModelGateway",
    "GatewayError",
    "UNTRUSTED_OPEN",
    "UNTRUSTED_CLOSE",
]

T = TypeVar("T", bound=BaseModel)

UNTRUSTED_OPEN = "<untrusted_source_data>"
UNTRUSTED_CLOSE = "</untrusted_source_data>"


class GatewayError(RuntimeError):
    """Raised when a call cannot produce a valid, schema-conforming response."""


@dataclass(frozen=True)
class ModelSpec:
    """One selectable model, with the facts routing needs."""

    provider: str
    model: str
    family: str
    """Independence is enforced at family level: two models from one family are
    treated as correlated even when their names differ."""
    input_cost_per_mtok: Decimal
    output_cost_per_mtok: Decimal
    supports_structured_output: bool = True
    max_output_tokens: int = 4096

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        cost = (
            Decimal(input_tokens) * self.input_cost_per_mtok
            + Decimal(output_tokens) * self.output_cost_per_mtok
        ) / Decimal(1_000_000)
        return int((cost * Decimal(1_000_000)).quantize(Decimal(1)))


@dataclass
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None


class Provider(ABC):
    """A model backend. Implementations must not interpret finance semantics."""

    name: str = "provider"

    @abstractmethod
    def complete(
        self,
        *,
        spec: ModelSpec,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ModelResponse:
        """Return the model's raw text response."""


class OfflineDeterministicProvider(Provider):
    """A provider that runs with no network and no API key.

    This is not a mock bolted on for convenience. ForgeBench has to run in CI on
    every commit, and a quality gate that depends on a paid external service is a
    quality gate that gets disabled the first time it is flaky. This provider
    produces schema-valid, deterministic output derived from the evidence packet,
    so the *orchestration* - routing, correction loops, gate enforcement - is
    tested continuously even when model calls are not.
    """

    name = "offline"

    def __init__(self, seed: int = 11) -> None:
        self.seed = seed
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        spec: ModelSpec,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ModelResponse:
        self.calls.append((spec.key, system[:40]))
        payload = _synthesise(system=system, user=user, schema=schema)
        text = json.dumps(payload)
        return ModelResponse(
            text=text,
            input_tokens=max(len(system) + len(user), 1) // 4,
            output_tokens=max(len(text), 1) // 4,
        )


def _extract_evidence_ids(user: str, limit: int = 4) -> list[str]:
    ids: list[str] = []
    try:
        start = user.index("{")
        data = json.loads(user[start : user.rindex("}") + 1])
        for ref in data.get("evidence", [])[:limit]:
            if "id" in ref:
                ids.append(str(ref["id"]))
    except Exception:  # noqa: BLE001 - offline provider must never break a run
        pass
    if not ids:
        ids = re.findall(r"\b(?:TXN|SEED|OI|LN|ST)-[A-Za-z0-9\-]+", user)[:limit]
    return ids or ["evidence-unavailable"]


def _synthesise(*, system: str, user: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    """Build a schema-valid response from the packet, deterministically."""
    role = "bookkeeper"
    m = re.search(r"You are the ([a-z_]+)", system)
    if m:
        role = m.group(1)
    evidence = _extract_evidence_ids(user)

    if "ReviewDecision" in (schema or {}).get("title", ""):
        return {
            "role": role,
            "decision": "approve",
            "evidence_test_passed": True,
            "control_test_passed": True,
            "strongest_alternative": (
                "The condition could reflect a legitimate but unusual transaction; "
                "rejected because the deterministic calculations in the packet "
                "reproduce the exception exactly."
            ),
            "notes": [],
            "residual_risk": (
                "Offline review: the deterministic evidence was checked, but no model "
                "judgement was applied to this item."
            ),
            "approved_scope": "analysis_only",
            "escalate_to": None,
            "escalation_reason": None,
        }

    return {
        "role": role,
        "conclusion": (
            "The control condition is reproduced by the deterministic calculations in "
            "the evidence packet. Prepared offline without model reasoning."
        ),
        "supporting_evidence_ids": evidence,
        "calculations_relied_on": [],
        "assumptions": ["Evidence packet is complete for the stated objective"],
        "alternative_explanations": [],
        "missing_evidence": [],
        "confidence": "0.5",
        "confidence_reason": (
            "Offline deterministic preparer: confidence reflects the control's own "
            "certainty, not model judgement."
        ),
        "proposed_action": {
            "kind": "investigate",
            "summary": "Review the evidence packet and confirm the control conclusion.",
            "reversible": True,
            "requires_human_approval": True,
            "estimated_value": None,
        },
    }


class AnthropicProvider(Provider):
    """Claude backend. Imported lazily so the core never depends on the SDK."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def _ensure(self):
        if self._client is None:
            if not self._api_key:
                raise GatewayError(
                    "ANTHROPIC_API_KEY is not set; use the offline provider or supply a key"
                )
            try:
                import anthropic  # noqa: PLC0415 - deliberate lazy import
            except ImportError as exc:  # pragma: no cover
                raise GatewayError(
                    "the anthropic package is not installed; pip install 'forge-core[anthropic]'"
                ) from exc
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(
        self,
        *,
        spec: ModelSpec,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ModelResponse:
        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": min(max_tokens, spec.max_output_tokens),
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if schema is not None:
            # A single forced tool is the most portable way to get strict JSON out
            # of a chat model without depending on a provider-specific mode.
            kwargs["tools"] = [
                {
                    "name": "submit",
                    "description": "Submit the structured result.",
                    "input_schema": schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "submit"}
        response = client.messages.create(**kwargs)
        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                text = json.dumps(block.input)
                break
            if getattr(block, "type", None) == "text":
                text += block.text
        return ModelResponse(
            text=text,
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            raw=response,
        )


# Default catalogue. Prices are illustrative defaults and are meant to be
# supplied from configuration in a deployment; they exist here so that cost
# telemetry is exercised rather than left as a TODO.
DEFAULT_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("offline", "deterministic", "offline", Decimal(0), Decimal(0)),
    ModelSpec("anthropic", "claude-haiku-4-5-20251001", "claude",
              Decimal("1.00"), Decimal("5.00")),
    ModelSpec("anthropic", "claude-sonnet-5", "claude", Decimal("3.00"), Decimal("15.00")),
    ModelSpec("anthropic", "claude-opus-5", "claude", Decimal("15.00"), Decimal("75.00")),
)


@dataclass
class ModelGateway:
    """Selects a model for a role and risk tier, calls it, and validates the result."""

    providers: dict[str, Provider] = field(default_factory=dict)
    models: tuple[ModelSpec, ...] = DEFAULT_MODELS
    default_provider: str = "offline"
    calls: list[AgentCall] = field(default_factory=list)
    max_attempts: int = 3

    @classmethod
    def offline(cls) -> "ModelGateway":
        return cls(providers={"offline": OfflineDeterministicProvider()})

    @classmethod
    def anthropic(cls, api_key: str | None = None) -> "ModelGateway":
        return cls(
            providers={
                "anthropic": AnthropicProvider(api_key),
                "offline": OfflineDeterministicProvider(),
            },
            default_provider="anthropic",
        )

    # ---- routing -------------------------------------------------------

    def select(
        self,
        *,
        tier: RiskTier,
        role: AgentRole,
        avoid_family: str | None = None,
    ) -> ModelSpec:
        """Pick a model for this tier and role, optionally avoiding a family.

        ``avoid_family`` is how reviewer independence is enforced. When no
        independent family is configured the gateway does not silently pretend
        independence: it returns the best available model, and the caller records
        that the review was not independent.
        """
        available = [m for m in self.models if m.provider in self.providers]
        if not available:
            raise GatewayError("no models available for any configured provider")

        preferred = [m for m in available if m.provider == self.default_provider] or available
        if avoid_family:
            independent = [m for m in preferred if m.family != avoid_family]
            if independent:
                preferred = independent

        ranked = sorted(preferred, key=lambda m: m.input_cost_per_mtok)
        if tier.level >= 4 or role in (AgentRole.CFO, AgentRole.ADVERSARY, AgentRole.VP_FINANCE):
            return ranked[-1]
        if tier.level == 3 or role in (AgentRole.CONTROLLER, AgentRole.POLICY_REVIEWER):
            return ranked[min(len(ranked) - 1, max(len(ranked) - 2, 0))]
        if tier.level == 2:
            return ranked[min(1, len(ranked) - 1)]
        return ranked[0]

    # ---- invocation ----------------------------------------------------

    def call(
        self,
        *,
        role: AgentRole,
        tier: RiskTier,
        system: str,
        user: str,
        response_model: Type[T],
        packet_checksum: str,
        prompt_version: str = "1",
        avoid_family: str | None = None,
        spec: ModelSpec | None = None,
    ) -> tuple[T, AgentCall]:
        """Call a model and return a validated response, or raise.

        A response that fails validation is retried with the validation error fed
        back. After ``max_attempts`` the call fails loudly. Constitution rule #6:
        no silent uncertainty, and that includes the plumbing.
        """
        chosen = spec or self.select(tier=tier, role=role, avoid_family=avoid_family)
        provider = self.providers[chosen.provider]
        schema = response_model.model_json_schema()
        schema.setdefault("title", response_model.__name__)

        attempt_user = user
        last_error: str | None = None
        started = time.perf_counter()
        total_in = total_out = 0

        for attempt in range(1, self.max_attempts + 1):
            response = provider.complete(
                spec=chosen,
                system=system,
                user=attempt_user,
                schema=schema,
                max_tokens=chosen.max_output_tokens,
            )
            total_in += response.input_tokens
            total_out += response.output_tokens
            try:
                parsed = response_model.model_validate_json(response.text)
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)[:1500]
                attempt_user = (
                    f"{user}\n\nYour previous response did not validate against the required "
                    f"schema. Fix exactly these problems and resubmit:\n{last_error}"
                )
                continue

            call = AgentCall(
                role=role,
                provider=chosen.provider,
                model=chosen.model,
                prompt_version=prompt_version,
                packet_checksum=packet_checksum,
                input_tokens=total_in,
                output_tokens=total_out,
                latency_ms=(time.perf_counter() - started) * 1000,
                cost_micros=chosen.cost_micros(total_in, total_out),
                succeeded=True,
            )
            self.calls.append(call)
            return parsed, call

        call = AgentCall(
            role=role,
            provider=chosen.provider,
            model=chosen.model,
            prompt_version=prompt_version,
            packet_checksum=packet_checksum,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=(time.perf_counter() - started) * 1000,
            cost_micros=chosen.cost_micros(total_in, total_out),
            succeeded=False,
            error=last_error,
        )
        self.calls.append(call)
        raise GatewayError(
            f"{role.value} response failed validation after {self.max_attempts} attempts: "
            f"{last_error}"
        )

    # ---- telemetry -----------------------------------------------------

    @property
    def total_cost_micros(self) -> int:
        return sum(c.cost_micros for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)

    def cost_summary(self) -> dict[str, Any]:
        by_role: dict[str, int] = {}
        for c in self.calls:
            by_role[c.role.value] = by_role.get(c.role.value, 0) + c.cost_micros
        return {
            "calls": len(self.calls),
            "failed_calls": sum(1 for c in self.calls if not c.succeeded),
            "tokens": self.total_tokens,
            "cost_micros": self.total_cost_micros,
            "cost": f"{Decimal(self.total_cost_micros) / Decimal(1_000_000):.4f}",
            "by_role": by_role,
        }
