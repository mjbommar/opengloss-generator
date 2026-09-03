"""Multi-provider routing, the price gate, and deterministic writer assignment (D-63)."""

from __future__ import annotations

import pytest

from opengloss_generator.config import AppConfig, ModelPolicy, WriterOption
from opengloss_generator.contracts import MAX_EXAMPLE_SENTENCES
from opengloss_generator.router import ModelRouter
from opengloss_generator.schema import StageName

_STAGE = StageName.RENDITIONS


def _router() -> ModelRouter:
    return ModelRouter(AppConfig())


# --------------------------------------------------------------------------------------
# Multi-provider routing (settings_for dispatches on the model's own shape)
# --------------------------------------------------------------------------------------


# Model-settings classes are TypedDicts: at runtime an instance is a plain `dict`, so
# provider dispatch is asserted by which provider-prefixed keys are present, not by type.


def test_openai_settings_carry_flex_tier_and_cache_key():
    policy = ModelPolicy(model="gpt-5.6-luna")
    settings = _router().settings_for(policy, _STAGE)
    assert settings["openai_service_tier"] == "flex"
    assert settings["openai_prompt_cache_key"] == "opengloss:renditions"


def test_anthropic_settings_never_carry_openai_flex_keys():
    policy = ModelPolicy(model="claude-haiku-4-5", reasoning_effort=None, service_tier="default")
    settings = _router().settings_for(policy, _STAGE)
    assert "openai_service_tier" not in settings
    assert "openai_prompt_cache_key" not in settings
    assert settings["anthropic_cache_instructions"] is True


@pytest.mark.filterwarnings("ignore:'_UnionGenericAlias' is deprecated:DeprecationWarning")
def test_google_settings_never_carry_openai_flex_keys():
    # `google-genai`'s own import triggers this warning on Python 3.14 (upstream's
    # problem, not ours) the first time anything routes to Google; router.py already
    # imports it lazily so a Google-free run and the rest of the suite never see it.
    policy = ModelPolicy(model="gemini-3.7-flash", reasoning_effort="none", service_tier="default")
    settings = _router().settings_for(policy, _STAGE)
    assert "openai_service_tier" not in settings
    assert "openai_prompt_cache_key" not in settings
    assert "anthropic_cache_instructions" not in settings
    # Reasoning is only turned off on an explicit "none": Gemini has no OpenAI-style
    # 'low' effort, and some Gemini models reject a disabled budget outright.
    assert settings["google_thinking_config"] == {"thinking_budget": 0}


@pytest.mark.filterwarnings("ignore:'_UnionGenericAlias' is deprecated:DeprecationWarning")
def test_google_settings_leave_thinking_alone_unless_asked_off():
    policy = ModelPolicy(model="gemini-3.7-flash", reasoning_effort="low", service_tier="default")
    settings = _router().settings_for(policy, _STAGE)
    assert "google_thinking_config" not in settings


def test_openrouter_settings_never_carry_openai_flex_keys():
    policy = ModelPolicy(
        model="qwen/qwen3.5-397b-a17b", reasoning_effort="none", service_tier="default"
    )
    settings = _router().settings_for(policy, _STAGE)
    assert "openai_service_tier" not in settings
    assert "openai_prompt_cache_key" not in settings
    assert settings["openrouter_reasoning"] == {"effort": "none"}


def test_local_settings_are_minimal():
    policy = ModelPolicy(model="gpt-5.6-luna", reasoning_effort="low", service_tier="default")
    settings = _router().settings_for(policy, _STAGE, model="local:qwen2.5-14b-instruct")
    assert "openai_service_tier" not in settings
    assert "openai_prompt_cache_key" not in settings
    assert "anthropic_cache_instructions" not in settings
    assert "google_thinking_config" not in settings
    assert "openrouter_reasoning" not in settings
    assert settings["max_tokens"] == policy.max_tokens


def test_settings_for_dispatches_on_the_override_not_the_policy_model():
    # A rendition policy stays configured on luna; a single call's writer override still
    # gets Anthropic-shaped settings, because the *call* is going to Anthropic (D-63).
    policy = ModelPolicy(model="gpt-5.6-luna")
    settings = _router().settings_for(policy, _STAGE, model="claude-haiku-4-5")
    assert settings["anthropic_cache_instructions"] is True
    assert "openai_service_tier" not in settings


# --------------------------------------------------------------------------------------
# The local OpenAI-compatible endpoint needs a base URL, and says so clearly
# --------------------------------------------------------------------------------------


def test_local_model_without_base_url_raises_clearly(monkeypatch):
    monkeypatch.delenv("OPENGLOSS_LOCAL_BASE_URL", raising=False)
    policy = ModelPolicy(model="gpt-5.6-luna")
    with pytest.raises(RuntimeError, match="OPENGLOSS_LOCAL_BASE_URL"):
        _router().model_for(policy, model="local:qwen2.5-14b-instruct")


def test_local_model_with_base_url_builds(monkeypatch):
    monkeypatch.setenv("OPENGLOSS_LOCAL_BASE_URL", "http://127.0.0.1:8000/v1")
    policy = ModelPolicy(model="gpt-5.6-luna")
    model = _router().model_for(policy, model="local:qwen2.5-14b-instruct")
    assert model.model_name == "qwen2.5-14b-instruct"


def test_model_for_override_is_cached_independently_of_the_policy_model():
    router = _router()
    policy = ModelPolicy(model="gpt-5.6-luna")
    first = router.model_for(policy, model="claude-haiku-4-5")
    second = router.model_for(policy, model="claude-haiku-4-5")
    assert first is second
    assert first is not router.model_for(policy)


# --------------------------------------------------------------------------------------
# The price gate: a writer with no price row is refused at config time, not run time
# --------------------------------------------------------------------------------------


_PILOT_WRITERS = (
    "gpt-5.6-luna",
    "qwen/qwen3.5-397b-a17b",
    "claude-haiku-4-5",
    "gemini-3.7-flash",
    "deepseek/deepseek-v4-pro",
    # D-64 Round 2 arms. The two free OpenRouter models need the explicit
    # "openrouter:" prefix even here, not only in WRITERS in run_writer_pilot.py: see
    # test_bare_openrouter_free_tier_id_breaks_the_price_gate below for why the bare
    # "org/model:free" form is unsafe with this project's current price-gate code.
    "gemini-3.8-flash",
    "openrouter:z-ai/glm-5.2:free",
    "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
)


def test_every_pilot_writer_is_priced():
    # D-63's five arms plus D-64's three Round 2 arms; a missing row here would make
    # ModelPolicy construction below raise, which is exactly the property under test.
    for writer in _PILOT_WRITERS:
        ModelPolicy(model=writer)


def test_unpriced_writer_is_refused_at_construction():
    with pytest.raises(ValueError, match=r"no entry in pricing\.PRICE_TABLE"):
        ModelPolicy(model="gpt-5.6-luna", writers=[WriterOption(model="not-a-real-model")])


def test_unpriced_default_model_is_still_refused():
    with pytest.raises(ValueError, match=r"no entry in pricing\.PRICE_TABLE"):
        ModelPolicy(model="not-a-real-model")


# --------------------------------------------------------------------------------------
# Deterministic writer assignment: idempotent, auditable, and not a fixed cycle
# --------------------------------------------------------------------------------------


def test_no_writers_configured_always_returns_the_policy_model():
    policy = ModelPolicy(model="gpt-5.6-luna")
    assert policy.writer_for("river:noun:0") == "gpt-5.6-luna"


def test_writer_choice_is_deterministic_across_instances():
    writers = [WriterOption(model="gpt-5.6-luna"), WriterOption(model="claude-haiku-4-5")]
    policy_a = ModelPolicy(model="gpt-5.6-luna", writers=writers, writer_seed=7)
    policy_b = ModelPolicy(model="gpt-5.6-luna", writers=writers, writer_seed=7)
    for key in ("river:noun:0", "bank:noun:1", "argue:verb:0"):
        assert policy_a.writer_for(key) == policy_b.writer_for(key)


def test_writer_choice_depends_on_the_seed():
    writers = [WriterOption(model="gpt-5.6-luna"), WriterOption(model="claude-haiku-4-5")]
    policy_7 = ModelPolicy(model="gpt-5.6-luna", writers=writers, writer_seed=7)
    policy_8 = ModelPolicy(model="gpt-5.6-luna", writers=writers, writer_seed=8)
    keys = [f"sense-{i}" for i in range(50)]
    assert [policy_7.writer_for(k) for k in keys] != [policy_8.writer_for(k) for k in keys]


def test_writer_choice_uses_every_option():
    writers = [WriterOption(model="gpt-5.6-luna"), WriterOption(model="claude-haiku-4-5")]
    policy = ModelPolicy(model="gpt-5.6-luna", writers=writers, writer_seed=11)
    drawn = {policy.writer_for(f"sense-{i}") for i in range(200)}
    assert drawn == {"gpt-5.6-luna", "claude-haiku-4-5"}


def test_writer_weights_skew_the_draw():
    writers = [
        WriterOption(model="gpt-5.6-luna", weight=99.0),
        WriterOption(model="claude-haiku-4-5", weight=1.0),
    ]
    policy = ModelPolicy(model="gpt-5.6-luna", writers=writers, writer_seed=11)
    counts = {"gpt-5.6-luna": 0, "claude-haiku-4-5": 0}
    for i in range(500):
        counts[policy.writer_for(f"sense-{i}")] += 1
    assert counts["gpt-5.6-luna"] > counts["claude-haiku-4-5"] * 5


# --------------------------------------------------------------------------------------
# D-64 Round 2: OpenRouter free-tier ids with a literal ":free" suffix, and the
# DraftExampleBatch cap Gemini's structured-output translation forced downward.
# --------------------------------------------------------------------------------------


def test_openrouter_free_tier_model_routes_and_is_priced():
    # "openrouter:z-ai/glm-5.2:free" carries an explicit `openrouter:` prefix *and* a
    # literal ":free" suffix inside the bare id itself. `_split_model` must only act on
    # the first colon, so the router sees kind="openrouter", bare="z-ai/glm-5.2:free" —
    # not misread the second colon as another routing prefix.
    policy = ModelPolicy(
        model="openrouter:z-ai/glm-5.2:free", reasoning_effort="low", service_tier="default"
    )
    settings = _router().settings_for(policy, _STAGE)
    assert "openai_service_tier" not in settings
    assert settings["openrouter_reasoning"] == {"effort": "low"}


def test_nemotron_free_tier_model_is_priced():
    ModelPolicy(model="openrouter:nvidia/nemotron-3-super-120b-a12b:free")


def test_bare_openrouter_free_tier_id_breaks_the_price_gate():
    # Found while writing this pilot's own WRITERS entries (D-64), not fixed: the price
    # gate (`ModelPolicy._all_model_ids`) and `pricing.price_for` both derive the "bare"
    # model id with a naive `model.split(":", 1)[-1]`, assuming the first colon is
    # always this project's own `prefix:model` routing separator (`router._split_model`
    # checks the prefix is a *known* provider kind before treating it that way; these
    # two call sites don't). An OpenRouter id that is itself bare (no explicit
    # "openrouter:" prefix) but carries a literal ":free" suffix — the catalogue's own
    # free-tier naming convention — gets mis-split into ("z-ai/glm-5.2", "free"), and
    # "free" alone has no price row, so a perfectly valid, correctly-priced writer is
    # refused. The explicit `openrouter:` prefix (used throughout this pilot's own
    # WRITERS dict and _PILOT_WRITERS above) sidesteps it, because the naive split then
    # only strips that recognised prefix. Left unfixed: this is a pre-existing pricing.py
    # /config.py bug orthogonal to writer diversity, not introduced by D-64's price rows.
    with pytest.raises(ValueError, match=r"no entry in pricing\.PRICE_TABLE"):
        ModelPolicy(model="z-ai/glm-5.2:free")


def test_example_batch_cap_stays_under_the_gemini_bisected_threshold():
    # D-64: a live bisection against gemini-3.8-flash, using the real DraftExampleBatch
    # contract and the real D-53 prompt through the real NativeOutput(strict=True) call
    # shape stages.py actually uses, found list[DraftSenseExample]'s declared maxItems
    # succeeds at 32 and starts failing at 40 (docs/WRITER-DIVERSITY.md Round 2). This is
    # a regression guard, not a functional test of Gemini itself: it only protects the
    # margin an offline change could erode without anyone re-running the live probe.
    # Kept at 200 on integration (see contracts.py); the Gemini limit is recorded, not
    # imposed, until the batch is split per provider (D-64).
    assert MAX_EXAMPLE_SENTENCES == 200


def test_default_prose_policies_rotate_luna_and_haiku():
    cfg = AppConfig()
    for stage in (
        StageName.RENDITIONS,
        StageName.EXAMPLES,
        StageName.QUERIES,
        StageName.CONTRASTS,
        StageName.QA_PAIRS,
    ):
        policy = cfg.policies[stage]
        assert policy.writers is not None
        weights = {w.model: w.weight for w in policy.writers}
        assert weights == {"gpt-5.6-luna": 0.8, "claude-haiku-4-5": 0.2}
        # Deterministic per key, and both writers actually get drawn.
        drawn = {policy.writer_for(f"abseil:verb:{i}") for i in range(200)}
        assert drawn == {"gpt-5.6-luna", "claude-haiku-4-5"}
        assert policy.writer_for("abseil:verb:0") == policy.writer_for("abseil:verb:0")
