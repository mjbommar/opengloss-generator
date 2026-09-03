"""Model construction, provider settings, and service-tier management.

The router owns two things the rest of the system should not care about:

* turning a :class:`~opengloss_generator.config.ModelPolicy` into a concrete pydantic-ai
  model plus settings, including the cost-saving defaults (flex tier, prompt cache key,
  reasoning effort);
* the flex fallback. Flex returns ``429 resource_unavailable`` when OpenAI is short of
  capacity — unbilled, but a failure. After a configurable number of *consecutive*
  rejections the router downgrades the whole run to ``service_tier="auto"`` and says so,
  rather than stalling on retries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
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


def _provider_prefix(model: str) -> str:
    """Return the pydantic-ai provider prefix for a bare model id."""
    if model.startswith(_ANTHROPIC_PREFIXES):
        return "anthropic"
    return "openai"


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

    def model_for(self, policy: ModelPolicy) -> Model:
        """Return (and memoise) the pydantic-ai model for a policy.

        Args:
            policy: The stage's model policy.

        Returns:
            A pydantic-ai :class:`~pydantic_ai.models.Model`.
        """
        bare = policy.model.split(":", 1)[-1]
        if bare not in self._models:
            self._models[bare] = infer_model(f"{_provider_prefix(bare)}:{bare}")
        return self._models[bare]

    def limiter_for(self, policy: ModelPolicy) -> RateLimiter:
        """Return (and memoise) the rate limiter for a policy's model."""
        bare = policy.model.split(":", 1)[-1]
        if bare not in self._limiters:
            self._limiters[bare] = RateLimiter(
                requests_per_minute=self._config.concurrency.requests_per_minute,
                tokens_per_minute=self._config.concurrency.tokens_per_minute,
            )
        return self._limiters[bare]

    def effective_tier(self, policy: ModelPolicy) -> ServiceTier:
        """Return the tier a call should actually use.

        Applies the run-level flex downgrade, so a capacity-starved run does not keep
        retrying a tier that is not serving it.

        Args:
            policy: The stage's model policy.

        Returns:
            The service tier to send, and to price the call at.
        """
        if policy.service_tier is ServiceTier.FLEX and self._flex_disabled:
            return ServiceTier.AUTO
        return policy.service_tier

    def settings_for(self, policy: ModelPolicy, stage: StageName) -> ModelSettings:
        """Build the model settings for a stage call.

        The prompt cache key is set per stage: same-stage calls share a static
        instruction prefix, so routing them to one cache raises the hit rate, and cached
        input is an order of magnitude cheaper than fresh input.

        Args:
            policy: The stage's model policy.
            stage: The stage being run, used as the cache key.

        Returns:
            Provider-appropriate model settings.
        """
        bare = policy.model.split(":", 1)[-1]
        common: dict[str, object] = {
            "max_tokens": policy.max_tokens,
            "timeout": policy.timeout_seconds,
        }
        if policy.temperature is not None:
            common["temperature"] = policy.temperature

        if _provider_prefix(bare) == "anthropic":
            # The static instructions (the QA rubric is ~2.4K tokens) are byte-stable per
            # stage, so a cache breakpoint after them makes every call after the first pay
            # the cached rate on that prefix — measured at ~16% of the judge's cost (D-48).
            return AnthropicModelSettings(**common, anthropic_cache_instructions=True)  # type: ignore[typeddict-item]

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
