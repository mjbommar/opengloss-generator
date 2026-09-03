"""Multi-provider routing, the price gate, and deterministic writer assignment (D-63)."""

from __future__ import annotations

import pytest

from opengloss_generator.config import AppConfig, ModelPolicy, WriterOption
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
)


def test_every_pilot_writer_is_priced():
    # D-63's five arms; a missing row here would make ModelPolicy construction below
    # raise, which is exactly the property under test.
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
