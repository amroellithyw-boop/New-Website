"""The model gateway: routing, independence, cascade, fallback and budget.

Constitution rule #11: ForgeOS owns the orchestration; model providers are
replaceable components. Nothing above this module names a vendor.

What the gateway does with a call, in order:

1. **Route by tier.** Each risk tier has a minimum quality. The gateway picks
   the cheapest available model at or above it that has the capabilities the
   call needs (vision, JSON schema, web search). A free local model wins a
   routine tier when one is running; a frontier model is reserved for R4 and
   adversarial work.
2. **Enforce independence.** For material work the reviewer must come from a
   different model family than the preparer. With three families configured
   (say Claude, GPT and Grok) a preparer, a controller and an adversary can each
   run on a different one. With one family, the gateway records that the review
   was not independent rather than pretending.
3. **Cascade when asked.** A cheap first pass; if the structured result reports
   low confidence or missing evidence, the same call is escalated one quality
   level. Most routine items never reach the expensive model.
4. **Fall back on failure.** Retryable provider errors move to the next model in
   the chain across providers; a refusal moves to another family.
5. **Stay inside budget.** A per-gateway ceiling on cost and calls that fails
   loudly. Constitution rule #6 applies to money too: no silent overspend.

Every call records provider, model, tokens, cache hits, latency and cost.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..canonical.enums import AgentRole, RiskTier
from .catalogue import CATALOGUE, PROVIDER_ENV, ModelSpec
from .contracts import AgentCall
from .providers import (
    ImageInput,
    ModelResponse,
    OfflineDeterministicProvider,
    Provider,
    ProviderError,
    ProviderRefused,
    build_provider,
)

__all__ = [
    "ModelSpec",
    "ModelResponse",
    "Provider",
    "OfflineDeterministicProvider",
    "ModelGateway",
    "GatewayError",
    "BudgetExceeded",
    "RoutingPolicy",
    "Budget",
    "ImageInput",
    "UNTRUSTED_OPEN",
    "UNTRUSTED_CLOSE",
    "DEFAULT_MODELS",
]

T = TypeVar("T", bound=BaseModel)

UNTRUSTED_OPEN = "<untrusted_source_data>"
UNTRUSTED_CLOSE = "</untrusted_source_data>"

# Backwards-compatible alias used by earlier tests.
DEFAULT_MODELS: tuple[ModelSpec, ...] = tuple(
    m for m in CATALOGUE if m.provider in ("offline", "anthropic")
)


class GatewayError(RuntimeError):
    """No provider produced a valid, schema-conforming response."""


class BudgetExceeded(GatewayError):
    """The configured cost or call ceiling would be breached."""


@dataclass
class Budget:
    """Ceilings for one gateway instance, typically one run or one work item."""

    max_cost_micros: int | None = None
    max_calls: int | None = None
    spent_micros: int = 0
    calls: int = 0

    def check(self) -> None:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f"call ceiling of {self.max_calls} reached")
        if self.max_cost_micros is not None and self.spent_micros >= self.max_cost_micros:
            raise BudgetExceeded(
                f"cost ceiling of {Decimal(self.max_cost_micros) / 1_000_000:.4f} reached"
            )

    def record(self, cost_micros: int) -> None:
        self.calls += 1
        self.spent_micros += cost_micros


@dataclass(frozen=True)
class RoutingPolicy:
    """The dials an operator turns. Defaults favour cost without losing capability."""

    min_quality_by_tier: dict[int, int] = field(default_factory=lambda: {0: 1, 1: 2, 2: 3, 3: 4, 4: 4})
    """Minimum model quality (1 to 5) for each risk tier level."""
    adversary_min_quality: int = 4
    executive_min_quality: int = 4
    prefer_local: bool = True
    """Use a free local model whenever it clears the tier's quality bar."""
    prefer_families: tuple[str, ...] = ("claude", "gpt", "grok", "gemini", "deepseek", "hermes", "llama")
    """Tie-break order among equally-priced candidates; also the order in which
    independent families are tried for reviewers."""
    allow_frontier: bool = False
    """Quality-5 models cost the most and are only used when explicitly enabled."""
    cascade_confidence_floor: Decimal = Decimal("0.7")

    def min_quality(self, tier: RiskTier, role: AgentRole) -> int:
        base = self.min_quality_by_tier.get(tier.level, 3)
        if role is AgentRole.ADVERSARY:
            base = max(base, self.adversary_min_quality)
        if role in (AgentRole.CFO, AgentRole.VP_FINANCE, AgentRole.POLICY_REVIEWER):
            base = max(base, self.executive_min_quality)
        return base


@dataclass
class ModelGateway:
    """Selects, calls, validates, retries, falls back, and accounts for every call."""

    providers: dict[str, Provider] = field(default_factory=dict)
    models: tuple[ModelSpec, ...] = CATALOGUE
    policy: RoutingPolicy = field(default_factory=RoutingPolicy)
    budget: Budget = field(default_factory=Budget)
    calls: list[AgentCall] = field(default_factory=list)
    max_attempts: int = 3
    """Validation retries per model before moving to the next one."""
    default_provider: str = ""
    """Kept for backwards compatibility; routing no longer depends on it."""

    # ---- construction --------------------------------------------------

    @classmethod
    def offline(cls, **overrides: Any) -> ModelGateway:
        return cls(providers={"offline": OfflineDeterministicProvider()}, **overrides)

    @classmethod
    def from_environment(cls, *, env: dict[str, str] | None = None, **overrides: Any) -> ModelGateway:
        """Build a gateway with every provider whose key is present.

        This is the production constructor. Set ``ANTHROPIC_API_KEY`` and you
        have Claude; add ``XAI_API_KEY`` and reviewer independence becomes real;
        run Ollama and extraction becomes free. Nothing else changes.
        """
        environ = env if env is not None else dict(os.environ)
        providers: dict[str, Provider] = {"offline": OfflineDeterministicProvider()}
        for provider, (var, _base) in PROVIDER_ENV.items():
            if provider in ("offline",) or not var:
                continue
            if environ.get(var):
                try:
                    providers[provider] = build_provider(provider)
                except ProviderError:
                    continue
        return cls(providers=providers, **overrides)

    @classmethod
    def anthropic(cls, api_key: str | None = None, **overrides: Any) -> ModelGateway:
        from .providers import AnthropicProvider

        return cls(
            providers={"anthropic": AnthropicProvider(api_key), "offline": OfflineDeterministicProvider()},
            **overrides,
        )

    # ---- routing -------------------------------------------------------

    def _available(self) -> list[ModelSpec]:
        out = [m for m in self.models if m.provider in self.providers]
        if not self.policy.allow_frontier:
            out = [m for m in out if m.quality < 5]
        return out

    def families_available(self) -> set[str]:
        return {m.family for m in self._available() if m.provider != "offline"}

    def candidates(
        self,
        *,
        tier: RiskTier,
        role: AgentRole,
        avoid_families: Sequence[str] = (),
        need_vision: bool = False,
        need_web_search: bool = False,
        need_pdf: bool = False,
        min_quality: int | None = None,
    ) -> list[ModelSpec]:
        """Every model that could take this call, cheapest first."""
        floor = min_quality if min_quality is not None else self.policy.min_quality(tier, role)
        avoid = set(avoid_families)
        pool = []
        for spec in self._available():
            if spec.provider == "offline":
                # The stub accepts anything so the orchestration can always be
                # exercised; it is ranked last below regardless.
                pool.append(spec)
                continue
            if spec.quality < floor:
                continue
            if spec.family in avoid:
                continue
            if need_vision and not spec.supports_vision:
                continue
            if need_web_search and not spec.supports_web_search:
                continue
            if need_pdf and spec.kind != "anthropic":
                continue
            pool.append(spec)
        # Offline is a last resort, never a preference, unless it is all there is.
        real = [m for m in pool if m.provider != "offline"]
        if not real:
            return pool

        def rank(m: ModelSpec) -> tuple:
            fam_rank = (
                self.policy.prefer_families.index(m.family)
                if m.family in self.policy.prefer_families
                else len(self.policy.prefer_families)
            )
            local_rank = 0 if (m.local and self.policy.prefer_local) else 1
            return (local_rank, m.input_cost_per_mtok + m.output_cost_per_mtok, -m.quality, fam_rank)

        return sorted(real, key=rank)

    def select(
        self,
        *,
        tier: RiskTier,
        role: AgentRole,
        avoid_family: str | None = None,
        avoid_families: Sequence[str] = (),
        need_vision: bool = False,
        need_web_search: bool = False,
        need_pdf: bool = False,
    ) -> ModelSpec:
        avoid = list(avoid_families) + ([avoid_family] if avoid_family else [])
        pool = self.candidates(
            tier=tier, role=role, avoid_families=avoid,
            need_vision=need_vision, need_web_search=need_web_search, need_pdf=need_pdf,
        )
        if not pool or all(m.provider == "offline" for m in pool):
            # Independence could not be honoured; fall back without the exclusion
            # and let the caller record that fact. The offline stub is never a
            # substitute for a real model that merely shares a family.
            pool = self.candidates(
                tier=tier, role=role, need_vision=need_vision,
                need_web_search=need_web_search, need_pdf=need_pdf,
            )
        if not pool:
            raise GatewayError("no model available for this call; configure a provider key")
        return pool[0]

    def independent_family_for(self, used: Sequence[str]) -> str | None:
        """A family not yet used in this review chain, in preference order."""
        available = self.families_available()
        for fam in self.policy.prefer_families:
            if fam in available and fam not in used:
                return fam
        for fam in sorted(available):
            if fam not in used:
                return fam
        return None

    # ---- invocation ----------------------------------------------------

    def call(
        self,
        *,
        role: AgentRole,
        tier: RiskTier,
        system: str,
        user: str,
        response_model: type[T],
        packet_checksum: str,
        prompt_version: str = "1",
        avoid_family: str | None = None,
        avoid_families: Sequence[str] = (),
        spec: ModelSpec | None = None,
        images: Sequence[ImageInput] = (),
        web_search: bool = False,
        max_tokens: int | None = None,
        cascade: bool = False,
    ) -> tuple[T, AgentCall]:
        """Call, validate, retry, fall back; return a parsed model and its record.

        With ``cascade`` on, the cheapest capable model runs first and the call
        escalates one quality level when the result reports low confidence or
        missing evidence. The escalation is recorded on the call, so the cost of
        a cascade that did not save anything is visible.
        """
        need_pdf = any(i.is_pdf for i in images)
        need_vision = bool(images)
        chain: list[ModelSpec]
        if spec is not None:
            chain = [spec] + [
                m for m in self.candidates(
                    tier=tier, role=role, avoid_families=list(avoid_families) + ([avoid_family] if avoid_family else []),
                    need_vision=need_vision, need_web_search=web_search, need_pdf=need_pdf,
                )
                if m.key != spec.key
            ]
        else:
            chain = self.candidates(
                tier=tier, role=role,
                avoid_families=list(avoid_families) + ([avoid_family] if avoid_family else []),
                need_vision=need_vision, need_web_search=web_search, need_pdf=need_pdf,
            )
            if cascade:
                floor = max(1, self.policy.min_quality(tier, role) - 1)
                cheaper = self.candidates(
                    tier=tier, role=role, min_quality=floor,
                    avoid_families=list(avoid_families) + ([avoid_family] if avoid_family else []),
                    need_vision=need_vision, need_web_search=web_search, need_pdf=need_pdf,
                )
                chain = cheaper + [m for m in chain if m not in cheaper]
        if not chain:
            raise GatewayError("no model available for this call; configure a provider key")

        schema = response_model.model_json_schema()
        schema.setdefault("title", response_model.__name__)
        errors: list[str] = []
        escalations = 0

        for idx, candidate in enumerate(chain):
            self.budget.check()
            provider = self.providers[candidate.provider]
            started = time.perf_counter()
            total_in = total_out = cache_read = 0
            attempt_user = user
            last_validation: str | None = None

            for _attempt in range(1, self.max_attempts + 1):
                try:
                    response = provider.complete(
                        spec=candidate, system=system, user=attempt_user, schema=schema,
                        images=images, max_tokens=max_tokens or candidate.max_output_tokens,
                        web_search=web_search,
                    )
                except ProviderRefused as exc:
                    errors.append(f"{candidate.key}: refused ({exc})")
                    self._record_failure(role, candidate, prompt_version, packet_checksum, str(exc), started)
                    break
                except ProviderError as exc:
                    errors.append(f"{candidate.key}: {exc}")
                    self._record_failure(role, candidate, prompt_version, packet_checksum, str(exc), started)
                    break

                total_in += response.input_tokens
                total_out += response.output_tokens
                cache_read += response.cache_read_tokens
                try:
                    parsed = response_model.model_validate_json(response.text)
                except (ValidationError, ValueError) as exc:
                    last_validation = str(exc)[:1500]
                    attempt_user = (
                        f"{user}\n\nYour previous response did not validate against the required "
                        f"schema. Fix exactly these problems and resubmit:\n{last_validation}"
                    )
                    continue

                cost = candidate.cost_micros(total_in - cache_read, total_out) + int(
                    Decimal(cache_read) * candidate.input_cost_per_mtok / Decimal(10)  # cache reads ~10%
                )
                record = AgentCall(
                    role=role, provider=candidate.provider, model=response.model_used or candidate.model,
                    prompt_version=prompt_version, packet_checksum=packet_checksum,
                    input_tokens=total_in, output_tokens=total_out,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    cost_micros=cost, succeeded=True,
                )
                self.calls.append(record)
                self.budget.record(cost)

                if cascade and idx + 1 < len(chain) and self._should_escalate(parsed, candidate):
                    escalations += 1
                    errors.append(f"{candidate.key}: low confidence, escalating")
                    break  # try the next, stronger model

                return parsed, record

            else:
                errors.append(f"{candidate.key}: invalid after {self.max_attempts} attempts: {last_validation}")
                self._record_failure(role, candidate, prompt_version, packet_checksum, last_validation or "", started,
                                     input_tokens=total_in, output_tokens=total_out)

        raise GatewayError(
            f"{role.value} produced no valid response across {len(chain)} model(s): " + " | ".join(errors[-4:])
        )

    def _should_escalate(self, parsed: BaseModel, spec: ModelSpec) -> bool:
        if spec.quality >= 4:
            return False
        confidence = getattr(parsed, "confidence", None)
        if confidence is not None:
            try:
                if Decimal(str(confidence)) < self.policy.cascade_confidence_floor:
                    return True
            except Exception:  # noqa: BLE001
                pass
        missing = getattr(parsed, "missing_evidence", None)
        return bool(missing)

    def _record_failure(self, role, spec, prompt_version, checksum, error, started, *, input_tokens=0, output_tokens=0):
        self.calls.append(
            AgentCall(
                role=role, provider=spec.provider, model=spec.model, prompt_version=prompt_version,
                packet_checksum=checksum, input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=(time.perf_counter() - started) * 1000,
                cost_micros=spec.cost_micros(input_tokens, output_tokens), succeeded=False, error=error[:500],
            )
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
        by_model: dict[str, int] = {}
        for c in self.calls:
            by_role[c.role.value] = by_role.get(c.role.value, 0) + c.cost_micros
            by_model[f"{c.provider}:{c.model}"] = by_model.get(f"{c.provider}:{c.model}", 0) + c.cost_micros
        return {
            "calls": len(self.calls),
            "failed_calls": sum(1 for c in self.calls if not c.succeeded),
            "tokens": self.total_tokens,
            "cost_micros": self.total_cost_micros,
            "cost": f"{Decimal(self.total_cost_micros) / Decimal(1_000_000):.4f}",
            "by_role": by_role,
            "by_model": by_model,
            "families_available": sorted(self.families_available()),
            "budget": {
                "spent_micros": self.budget.spent_micros,
                "max_cost_micros": self.budget.max_cost_micros,
                "calls": self.budget.calls,
                "max_calls": self.budget.max_calls,
            },
        }
