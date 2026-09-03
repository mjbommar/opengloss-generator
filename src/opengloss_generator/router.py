"""Model construction, provider settings, and service-tier management.

The router owns two things the rest of the system should not care about:

* turning a :class:`~opengloss_generator.config.ModelPolicy` into a concrete pydantic-ai
  model plus settings, including the cost-saving defaults (flex tier, prompt cache key,
  reasoning effort);
* the flex fallback. Flex returns ``429 resource_unavailable`` when OpenAI is short of
  capacity — unbilled, but a failure. After a configurable number of *consecutive*
  rejections the router downgrades the whole run to ``service_tier="auto"`` and says so,
  rather than stalling on retries.

D-63 (writer-diversity pilot) added multi-provider routing: a policy's model may now
name an Anthropic, Google, OpenRouter, or local OpenAI-compatible model, not only
OpenAI's. Every provider still goes through the same two entry points
(:meth:`ModelRouter.model_for` and :meth:`ModelRouter.settings_for`), each taking an
optional ``model`` override so a single call can use a different writer than the rest of
its stage (see :meth:`~opengloss_generator.config.ModelPolicy.writer_for`) without
touching the policy itself. Flex-tier settings are built only in the OpenAI branch, so
they are never sent to a provider that would reject them outright.
"""

from __future__ import annotations

import os
import warnings
from typing import TYPE_CHECKING

from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModelSettings
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from opengloss_generator.log import get_logger
from opengloss_generator.pricing import ServiceTier
from opengloss_generator.ratelimit import RateLimiter

if TYPE_CHECKING:
    from opengloss_generator.config import AppConfig, ModelPolicy
    from opengloss_generator.schema import StageName

__all__ = ["ModelRouter", "estimate_tokens"]

_LOG = get_logger(__name__)

# Deliberately pessimistic: English averages closer to 4 characters per token, so 3.0
# over-reserves. Over-reserving costs throughput; under-reserving costs 429s.
_CHARS_PER_TOKEN = 3.0

_ANTHROPIC_PREFIXES = ("claude-",)
_GOOGLE_PREFIXES = ("gemini-",)
# Recognised explicit routing prefixes, ``prefix:model``. A model with none of these is
# routed by the shape of its own id (see :func:`_split_model`) — the pre-D-63 convention,
# kept as the default so existing bare OpenAI and Anthropic ids need no config change.
_KNOWN_PROVIDER_KINDS = frozenset({"openai", "anthropic", "google", "openrouter", "local"})
# A local, OpenAI-compatible endpoint (e.g. a vLLM server) has no registered pydantic-ai
# provider name, since it is not a fixed catalogue but a base URL the operator supplies.
_LOCAL_BASE_URL_ENV = "OPENGLOSS_LOCAL_BASE_URL"
_LOCAL_API_KEY_ENV = "OPENGLOSS_LOCAL_API_KEY"
# `pydantic_ai.models.google` imports `google-genai`, which on this project's Python
# 3.14 interpreter raises this exact `DeprecationWarning` at import time (an internal
# `typing` alias `google-genai` still touches) — upstream's problem, not this
# project's or that library's own code. The project's own `filterwarnings = ["error"]`
# would otherwise turn a warning about an unrelated library into a test failure for
# every test that so much as builds a Google model or its settings.
_GOOGLE_GENAI_DEPRECATION_MESSAGE = ".*_UnionGenericAlias.*"


def _import_google_model_settings() -> type[ModelSettings]:
    """Import and return ``GoogleModelSettings``, suppressing the warning above."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=DeprecationWarning, message=_GOOGLE_GENAI_DEPRECATION_MESSAGE
        )
        from pydantic_ai.models.google import GoogleModelSettings  # noqa: PLC0415 - see docstring
    return GoogleModelSettings


def _infer_model(provider_kind: str, bare: str) -> Model:
    """Call :func:`pydantic_ai.models.infer_model`, suppressing the warning above.

    A plain wrapper for every provider except Google; harmless for the rest since the
    suppressed message never occurs outside the ``google-genai`` import.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=DeprecationWarning, message=_GOOGLE_GENAI_DEPRECATION_MESSAGE
        )
        return infer_model(f"{provider_kind}:{bare}")


def estimate_tokens(prompt: str, instructions: str, max_output_tokens: int) -> int:
    """Return a conservative upper bound on a call's total token count.

    Used to reserve rate-limiter capacity before the call, when the true count is not yet
    known. Reconciled against reported usage afterwards.

    Args:
        prompt: The user prompt.
        instructions: The system instructions.
        max_output_tokens: The configured output ceiling.

    Returns:
        Estimated total tokens for the request.
    """
    characters = len(prompt) + len(instructions)
    return int(characters / _CHARS_PER_TOKEN) + max_output_tokens


def _split_model(model: str) -> tuple[str, str]:
    """Return ``(provider_kind, bare_model_id)`` for a policy's model string.

    A model may carry an explicit routing prefix this project recognises
    (``openai:``, ``anthropic:``, ``google:``, ``openrouter:``, or ``local:``) or be
    bare, in which case the provider is inferred from the id's own shape: an Anthropic
    id starts with ``claude-``, a Gemini id starts with ``gemini-``, an OpenRouter id
    carries the catalogue's ``org/model`` convention (a literal ``/``), and everything
    else is OpenAI — the convention this project already used for Anthropic before
    D-63 added the rest.

    Args:
        model: The configured model string, from ``ModelPolicy.model`` or one drawn
            from ``ModelPolicy.writers``.

    Returns:
        ``(provider_kind, bare_model_id)``, where ``provider_kind`` is one of
        ``"openai"``, ``"anthropic"``, ``"google"``, ``"openrouter"``, or ``"local"``.
    """
    prefix, sep, rest = model.partition(":")
    if sep and prefix in _KNOWN_PROVIDER_KINDS and rest:
        return prefix, rest
    if model.startswith(_ANTHROPIC_PREFIXES):
        return "anthropic", model
    if model.startswith(_GOOGLE_PREFIXES):
        return "google", model
    if "/" in model:
        return "openrouter", model
    return "openai", model


def _build_local_model(bare: str) -> Model:
    """Build a model for a local, OpenAI-compatible endpoint named by base URL.

    For a local inference server (e.g. vLLM) speaking the OpenAI chat-completions API.
    There is no registered pydantic-ai provider name for an arbitrary endpoint, so the
    base URL — and, if the server checks one, an API key — come from this project's own
    environment variables instead.

    Args:
        bare: The model id the server should be asked for.

    Returns:
        An :class:`~pydantic_ai.models.openai.OpenAIChatModel` pointed at the endpoint.

    Raises:
        RuntimeError: If ``OPENGLOSS_LOCAL_BASE_URL`` is not set. Silently falling back
            to OpenAI's own endpoint would send a local-only model id there and fail
            with an unrelated "model not found" error instead of a clear one.
    """
    base_url = os.environ.get(_LOCAL_BASE_URL_ENV)
    if not base_url:
        raise RuntimeError(
            f"model {bare!r} uses the local: prefix but {_LOCAL_BASE_URL_ENV} is not set"
        )
    api_key = os.environ.get(_LOCAL_API_KEY_ENV, "local")
    return OpenAIChatModel(bare, provider=OpenAIProvider(base_url=base_url, api_key=api_key))


class ModelRouter:
    """Builds models and settings for stages, and owns run-level tier state.

    Args:
        config: The run configuration.
    """

    def __init__(self, config: AppConfig) -> None:
        """Cache models and limiters per model id and start un-downgraded."""
        self._config = config
        self._models: dict[str, Model] = {}
        self._limiters: dict[str, RateLimiter] = {}
        self._consecutive_flex_rejections = 0
        self._flex_disabled = False

    @property
    def flex_disabled(self) -> bool:
        """Return whether this run has fallen back off the flex tier."""
        return self._flex_disabled

    def model_for(self, policy: ModelPolicy, *, model: str | None = None) -> Model:
        """Return (and memoise) the pydantic-ai model for a policy, or an override.

        Args:
            policy: The stage's model policy.
            model: A specific model to build instead of ``policy.model`` — the writer
                a single call drew from ``policy.writers`` (D-63). Cached separately
                from the policy's own model, so a rotating writer only pays the
                construction cost once per distinct model, not once per policy.

        Returns:
            A pydantic-ai :class:`~pydantic_ai.models.Model`.
        """
        name = model if model is not None else policy.model
        if name not in self._models:
            kind, bare = _split_model(name)
            self._models[name] = (
                _build_local_model(bare) if kind == "local" else _infer_model(kind, bare)
            )
        return self._models[name]

    def limiter_for(self, policy: ModelPolicy, *, model: str | None = None) -> RateLimiter:
        """Return (and memoise) the rate limiter for a policy's model, or an override."""
        name = model if model is not None else policy.model
        if name not in self._limiters:
            self._limiters[name] = RateLimiter(
                requests_per_minute=self._config.concurrency.requests_per_minute,
                tokens_per_minute=self._config.concurrency.tokens_per_minute,
            )
        return self._limiters[name]

    def effective_tier(self, policy: ModelPolicy) -> ServiceTier:
        """Return the tier a call should actually use.

        Applies the run-level flex downgrade, so a capacity-starved run does not keep
        retrying a tier that is not serving it. Meaningless outside OpenAI (no other
        provider here has a flex tier), but harmless to compute unconditionally: only
        the OpenAI branch of :meth:`settings_for` ever reads it.

        Args:
            policy: The stage's model policy.

        Returns:
            The service tier to send, and to price the call at.
        """
        if policy.service_tier is ServiceTier.FLEX and self._flex_disabled:
            return ServiceTier.AUTO
        return policy.service_tier

    def settings_for(
        self, policy: ModelPolicy, stage: StageName, *, model: str | None = None
    ) -> ModelSettings:
        """Build the model settings for a stage call.

        The prompt cache key is set per stage: same-stage calls share a static
        instruction prefix, so routing them to one cache raises the hit rate, and cached
        input is an order of magnitude cheaper than fresh input. That is an OpenAI-only
        knob; every other provider gets only the shared basics plus what it can actually
        use, so a setting one provider rejects is never sent to it (D-63 — flex-tier and
        prompt-cache-key settings must only reach OpenAI).

        Args:
            policy: The stage's model policy.
            stage: The stage being run, used as the cache key.
            model: The model actually being called, if different from ``policy.model``
                (a writer drawn from ``policy.writers``, D-63). Only the *provider* this
                implies is used from it; tier, temperature, and token limits still come
                from ``policy``, since those describe the call, not the writer.

        Returns:
            Provider-appropriate model settings.
        """
        name = model if model is not None else policy.model
        kind, _ = _split_model(name)
        common: dict[str, object] = {
            "max_tokens": policy.max_tokens,
            "timeout": policy.timeout_seconds,
        }
        if policy.temperature is not None:
            common["temperature"] = policy.temperature

        if kind == "anthropic":
            # The static instructions (the QA rubric is ~2.4K tokens) are byte-stable per
            # stage, so a cache breakpoint after them makes every call after the first pay
            # the cached rate on that prefix — measured at ~16% of the judge's cost (D-48).
            return AnthropicModelSettings(**common, anthropic_cache_instructions=True)  # type: ignore[typeddict-item]

        if kind == "google":
            # Imported lazily (and warning-suppressed, see `_import_google_model_settings`):
            # every other provider's settings class is cheap to import eagerly; this one
            # only needs to load when a call actually routes to Google, which keeps every
            # Google-free run and the whole test suite unaffected by another project's warning.
            google_model_settings = _import_google_model_settings()
            google_settings: dict[str, object] = dict(common)
            # Only turned off on explicit request, and even then only via the one
            # request shape Gemini's thinking config accepts. Some Gemini models
            # reject it outright (SHELF, live: "Budget 0 is invalid. This model only
            # works in thinking mode.") — a policy that hits this should get that
            # error, not a silently-ignored setting, so it is not caught here.
            if policy.reasoning_effort == "none":
                google_settings["google_thinking_config"] = {"thinking_budget": 0}
            return google_model_settings(**google_settings)  # type: ignore[typeddict-item]

        if kind == "openrouter":
            openrouter_settings: dict[str, object] = dict(common)
            if policy.reasoning_effort is not None:
                # OpenRouter's own effort vocabulary ('none'|'low'|'medium'|'high'|...)
                # is a superset of this project's, so the value passes straight through.
                # Some OpenRouter endpoints reject "none" outright (SHELF, live:
                # "Reasoning is mandatory for this endpoint and cannot be disabled.") —
                # left uncaught for the same reason as the Google branch above.
                openrouter_settings["openrouter_reasoning"] = {"effort": policy.reasoning_effort}
            return OpenRouterModelSettings(**openrouter_settings)  # type: ignore[typeddict-item]

        if kind == "local":
            # A local OpenAI-compatible endpoint: no service tier, no prompt cache key,
            # and no reasoning-effort knob known to be supported, so only the basics.
            return ModelSettings(**common)  # type: ignore[typeddict-item]

        openai_settings: dict[str, object] = {
            **common,
            "openai_service_tier": self.effective_tier(policy).value,
            "openai_prompt_cache_key": f"opengloss:{stage.value}",
        }
        if policy.reasoning_effort is not None:
            openai_settings["openai_reasoning_effort"] = policy.reasoning_effort
        return OpenAIResponsesModelSettings(**openai_settings)  # type: ignore[typeddict-item]

    def note_flex_rejection(self) -> bool:
        """Record a flex capacity rejection and report whether the run downgraded.

        Returns:
            ``True`` if this rejection tripped the downgrade to ``auto``.
        """
        self._consecutive_flex_rejections += 1
        threshold = self._config.concurrency.max_flex_429s
        if not self._flex_disabled and self._consecutive_flex_rejections >= threshold:
            self._flex_disabled = True
            _LOG.warning(
                "flex_tier_downgraded",
                consecutive_rejections=self._consecutive_flex_rejections,
                threshold=threshold,
                new_tier=ServiceTier.AUTO.value,
            )
            return True
        return False

    def note_success(self) -> None:
        """Reset the consecutive-rejection counter after a call succeeds."""
        self._consecutive_flex_rejections = 0
