"""Model price table and cost arithmetic.

The table is data with an ``as_of`` date and a source URL, and ``tests/test_pricing.py``
asserts that every model the configuration can select has an entry — so adding a model
without its price is a test failure rather than a silent zero.

Prices verified 2026-09-02 against https://developers.openai.com/api/docs/pricing.
Anthropic prices from the bundled ``claude-api`` reference table (cached 2026-06-24).

The writer-diversity pilot's non-OpenAI, non-Anthropic rows (D-63) were verified
2026-09-03 against the OpenRouter model catalogue, ``GET
https://openrouter.ai/api/v1/models`` (unauthenticated; SHELF's ``pricing.py`` fetches
the same endpoint the same way). Each row's comment below records the catalogue's
per-token rate converted to this table's per-million convention, and a provider-page
cross-check where the model is also reachable outside OpenRouter.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "PRICE_TABLE",
    "PRICING_AS_OF",
    "PRICING_SOURCES",
    "ModelPrice",
    "ServiceTier",
    "UsageCost",
    "estimate_cost",
    "known_models",
    "price_for",
]

PRICING_AS_OF = dt.date(2026, 9, 2)
PRICING_SOURCES = (
    "https://developers.openai.com/api/docs/pricing",
    "https://developers.openai.com/api/docs/guides/flex-processing",
)

_PER_MILLION = 1_000_000


class ServiceTier(StrEnum):
    """OpenAI service tiers.

    ``FLEX`` is priced identically to the Batch API on the synchronous endpoint, which
    is why it is this project's default. ``FAST`` was renamed from ``priority`` on
    2026-07-30; the API accepts either spelling.
    """

    AUTO = "auto"
    DEFAULT = "default"
    FLEX = "flex"
    FAST = "priority"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Per-million-token rates for one model at one service tier."""

    model: str
    tier: ServiceTier
    input_usd: float
    cached_input_usd: float
    output_usd: float


@dataclass(frozen=True, slots=True)
class UsageCost:
    """The cost of one model call, decomposed by token class."""

    model: str
    tier: ServiceTier
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    input_usd: float
    cached_input_usd: float
    output_usd: float

    @property
    def total_usd(self) -> float:
        """Return the total cost of the call in US dollars."""
        return self.input_usd + self.cached_input_usd + self.output_usd


def _openai(
    model: str,
    std_in: float,
    std_cached: float,
    std_out: float,
) -> list[ModelPrice]:
    """Build the tier rows for an OpenAI model.

    Flex and Batch are half of standard for every model on the pricing page; Fast is
    double. Rather than transcribe all four rows per model we derive the multiples,
    which also keeps the ratios visible.
    """
    return [
        ModelPrice(model, ServiceTier.DEFAULT, std_in, std_cached, std_out),
        ModelPrice(model, ServiceTier.AUTO, std_in, std_cached, std_out),
        ModelPrice(model, ServiceTier.FLEX, std_in / 2, std_cached / 2, std_out / 2),
        ModelPrice(model, ServiceTier.FAST, std_in * 2, std_cached * 2, std_out * 2),
    ]


def _flat(model: str, in_usd: float, out_usd: float, cached_usd: float) -> list[ModelPrice]:
    """Build rows for a provider without service tiers (all tiers price the same)."""
    return [ModelPrice(model, tier, in_usd, cached_usd, out_usd) for tier in ServiceTier]


_ROWS: list[ModelPrice] = [
    # --- OpenAI, standard-tier rates from the pricing page (short-context band) ---
    *_openai("gpt-5.6-luna", 0.20, 0.02, 1.20),
    *_openai("gpt-5.6-terra", 2.00, 0.20, 12.00),
    *_openai("gpt-5.6-sol", 4.00, 0.40, 20.00),
    *_openai("gpt-5.5", 5.00, 0.50, 30.00),
    *_openai("gpt-5.4", 2.50, 0.25, 15.00),
    *_openai("gpt-5.4-mini", 0.75, 0.075, 4.50),
    *_openai("gpt-5.4-nano", 0.20, 0.02, 1.25),
    *_openai("gpt-5-mini", 0.25, 0.025, 2.00),
    *_openai("gpt-5-nano", 0.05, 0.005, 0.40),
    # --- Anthropic, used for the QA/judge path ---
    *_flat("claude-opus-5", 5.00, 25.00, 0.50),
    *_flat("claude-sonnet-5", 2.00, 10.00, 0.20),
    # Also a writer-diversity pilot arm (D-63), called direct (not via OpenRouter): the
    # OpenRouter catalogue's "anthropic/claude-haiku-4.5" row (fetched 2026-09-03) prices
    # identically ($1/$5/$0.10 per M) to this pre-existing row, so no change was needed.
    *_flat("claude-haiku-4-5", 1.00, 5.00, 0.10),
    # --- Writer-diversity pilot arms (D-63), OpenRouter and Google, no service tiers ---
    # OpenRouter catalogue "qwen/qwen3.5-397b-a17b", fetched 2026-09-03:
    # prompt $0.00000055, completion $0.0000035, input_cache_read $0.000000225 per token.
    *_flat("qwen/qwen3.5-397b-a17b", 0.55, 3.50, 0.225),
    # OpenRouter catalogue "deepseek/deepseek-v4-pro", fetched 2026-09-03:
    # prompt $0.000001039302, completion $0.000002078604, input_cache_read
    # $0.0000000866085 per token.
    *_flat("deepseek/deepseek-v4-pro", 1.039302, 2.078604, 0.0866085),
    # Called direct via the Google API (GEMINI_API_KEY/GOOGLE_API_KEY), not OpenRouter.
    # OpenRouter catalogue "google/gemini-3.7-flash", fetched 2026-09-03: prompt
    # $0.00000075, completion $0.00000375, input_cache_read $0.000000075 per token.
    # Cross-checked the same day against Google's own page,
    # https://ai.google.dev/gemini-api/docs/pricing ("Gemini 3.7 Flash", Paid Tier,
    # Standard): input $0.75, output (incl. thinking tokens) $3.75, context caching
    # $0.075 per million tokens through 2026-12-31 — an exact match, so the OpenRouter
    # rate is also this table's direct-API rate.
    *_flat("gemini-3.7-flash", 0.75, 3.75, 0.075),
]

PRICE_TABLE: dict[tuple[str, ServiceTier], ModelPrice] = {
    (row.model, row.tier): row for row in _ROWS
}


def known_models() -> frozenset[str]:
    """Return every model id the price table covers."""
    return frozenset(model for model, _ in PRICE_TABLE)


def price_for(model: str, tier: ServiceTier = ServiceTier.FLEX) -> ModelPrice:
    """Return the price row for a model at a service tier.

    Args:
        model: The provider's model identifier, with any ``provider:`` prefix stripped.
        tier: The service tier the call will use.

    Returns:
        The matching :class:`ModelPrice`.

    Raises:
        KeyError: If the model is not in the table. Failing loudly is deliberate:
            a missing price would otherwise report every run as free.
    """
    bare = model.split(":", 1)[-1]
    try:
        return PRICE_TABLE[(bare, tier)]
    except KeyError:
        raise KeyError(
            f"no price for model {bare!r} at tier {tier.value!r}; "
            f"add it to pricing.PRICE_TABLE (as_of {PRICING_AS_OF})"
        ) from None


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    tier: ServiceTier = ServiceTier.FLEX,
) -> UsageCost:
    """Compute the cost of a single model call from its reported usage.

    Providers report cached tokens *inside* ``input_tokens``, so the uncached input is
    ``input_tokens - cached_input_tokens``. Billing the full input at the fresh rate
    would overstate a cache-heavy workload by up to an order of magnitude.

    Args:
        model: Model identifier.
        input_tokens: Total prompt tokens as reported by the provider.
        output_tokens: Completion tokens as reported by the provider.
        cached_input_tokens: Prompt tokens served from cache, included in
            ``input_tokens``.
        tier: The service tier the call used.

    Returns:
        A :class:`UsageCost` decomposed by token class.

    Raises:
        ValueError: If token counts are negative or cached exceeds total input.
    """
    if min(input_tokens, output_tokens, cached_input_tokens) < 0:
        raise ValueError("token counts must be non-negative")
    if cached_input_tokens > input_tokens:
        raise ValueError(
            f"cached_input_tokens ({cached_input_tokens}) exceeds input_tokens ({input_tokens})"
        )

    rates = price_for(model, tier)
    fresh_input = input_tokens - cached_input_tokens
    return UsageCost(
        model=rates.model,
        tier=tier,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        input_usd=fresh_input * rates.input_usd / _PER_MILLION,
        cached_input_usd=cached_input_tokens * rates.cached_input_usd / _PER_MILLION,
        output_usd=output_tokens * rates.output_usd / _PER_MILLION,
    )
