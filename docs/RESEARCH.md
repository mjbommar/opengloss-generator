# Research log — 2026-09-02

Everything below was verified on 2026-09-02 against a live source, not recalled. Each
section names how it was checked so it can be re-run when it goes stale.

## 1. Toolchain

| Tool | Version | How checked |
|---|---|---|
| uv | 0.12.1 | `uv --version` |
| Python | 3.14.3 (project venv); 3.14.4 system; 3.15.0b4 available | `uv python list` |
| ruff | 0.15.4 | `ruff --version` |
| ty | 0.0.77 (in venv; 0.0.19 on PATH) | `uv sync` output |

Pinned `requires-python = ">=3.14"`. 3.15 is beta; not worth the churn.

## 2. Library versions (PyPI JSON API, 2026-09-02)

| Package | Latest | Notes |
|---|---|---|
| `pydantic-ai` | **2.37.0** | V2.0 released 2026-06-23; V1 idioms are stale (see § 3) |
| `pydantic` | 2.13.5 | |
| `pydantic-settings` | 2.15.0 | |
| `openai` | 3.7.0 | only needed for the Batch API path |
| `anthropic` | 1.3.0 | built on `httpx2`, not `httpx` |
| `structlog` | 26.1.0 | |
| `typer` | 0.27.2 | |

## 3. pydantic-ai 2.37 API — verified by introspecting the installed package

This matters because V1 tutorials (and my own prior) are wrong in specific ways.

```python
inspect.signature(Agent.__init__).parameters
# model, output_type, instructions, system_prompt, deps_type, name, description,
# model_settings, retries, validation_context, tools, toolsets, defer_model_check,
# end_strategy, metadata, tool_timeout, max_concurrency, capabilities
```

- **`result.usage` is a property, not a method.** `result.usage()` raises
  `TypeError: 'RunUsage' object is not callable`. This is a V1→V2 break.
- **`result.output`** holds the validated structured output (not `.data`).
- **`RunUsage` fields:** `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_write_tokens`, `input_audio_tokens`, `cache_audio_read_tokens`,
  `output_audio_tokens`, `details`, `cost`, `requests`, `tool_calls`.
  `cost` defaults to `None` and is only populated when pydantic-ai's bundled price data
  knows the model — it does not know the models we default to. **We compute cost
  ourselves** (FR-6) and treat `usage.cost` as a cross-check only.
- **`UsageLimits`** accepts `cost_limit`, `request_limit`, `input_tokens_limit`,
  `output_tokens_limit`, `total_tokens_limit`, `tool_calls_limit`,
  `per_request_input_tokens_limit`, `count_tokens_before_request`.
- **Concurrency is first-class**: `Agent(..., max_concurrency=...)`, plus
  `limit_model_concurrency`, `ConcurrencyLimiter`, `ConcurrencyLimitExceeded`,
  `ConcurrencyLimitedModel` exported from the package root.
- **Retry signal**: raise `pydantic_ai.ModelRetry` from an output validator to feed the
  error back to the model.
- **Offline testing**: `pydantic_ai.models.test.TestModel` and
  `pydantic_ai.models.function.FunctionModel` both import and run with no API key.

### Service tier

`ModelSettings` has a **unified `service_tier`** field, and
`OpenAIChatModelSettings` / `OpenAIResponsesModelSettings` additionally have
`openai_service_tier` (provider-specific wins when both are set). Accepted values:
`'auto'`, `'default'`, `'flex'`, `'priority'`.

Also present on the OpenAI settings and directly useful to us:
`openai_prompt_cache_key`, `openai_prompt_cache_retention`, `openai_prompt_cache_options`,
`openai_reasoning_effort`, `openai_service_tier`.

Source: <https://pydantic.dev/docs/ai/models/openai/> and package introspection.

## 4. OpenAI models available on this account

`GET https://api.openai.com/v1/models` (2026-09-02) returns, among others:
`gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.5`, `gpt-5.5-pro`,
`gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.4-pro`, `gpt-5.2`, `gpt-5.1`,
`gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `text-embedding-3-small`, `text-embedding-3-large`.

The v1.3 dataset used `gpt-5-nano` (core) and `gpt-5.4-mini` (gap-fill).

## 5. Pricing (developers.openai.com/api/docs/pricing, fetched 2026-09-02)

Per 1M tokens. "short"/"long" are the context-length bands. **Flex is priced identically
to Batch** for every model listed, which is the single most important fact for our
defaults: flex gets batch economics on the synchronous API.

| Model | Std in | Std cached | Std out | Flex/Batch in | Flex cached | Flex out |
|---|---|---|---|---|---|---|
| gpt-5.6-luna | 0.20 | 0.02 | 1.20 | 0.10 | 0.01 | 0.60 |
| gpt-5.6-terra | 2.00 | 0.20 | 12.00 | 1.00 | 0.10 | 6.00 |
| gpt-5.6-sol | 4.00 | 0.40 | 20.00 | 2.00 | 0.20 | 10.00 |
| gpt-5.5 | 5.00 | 0.50 | 30.00 | 2.50 | 0.25 | 15.00 |
| gpt-5.4 | 2.50 | 0.25 | 15.00 | 1.25 | 0.13 | 7.50 |
| gpt-5.4-mini | 0.75 | 0.075 | 4.50 | 0.375 | 0.0375 | 2.25 |
| gpt-5.4-nano | 0.20 | 0.02 | 1.25 | 0.10 | 0.01 | 0.625 |
| gpt-5-mini | 0.25 | 0.025 | 2.00 | 0.125 | 0.0125 | 1.00 |
| gpt-5-nano | 0.05 | 0.005 | 0.40 | 0.025 | 0.0025 | 0.20 |

Anthropic, for the QA/judge path (from the bundled `claude-api` skill's cached table,
2026-06-24): `claude-opus-5` $5/$25, `claude-sonnet-5` $2/$10, `claude-haiku-4-5` $1/$5.

Tier semantics, confirmed on the pricing page: Standard = baseline; **Batch ≈ 50% off**;
**Flex = same as Batch**; **Fast** (renamed from "priority" on 2026-07-30; both
`service_tier: "priority"` and `"fast"` are accepted) ≈ 2× standard.

## 6. Flex processing (developers.openai.com/api/docs/guides/flex-processing)

- Intended for "model evaluations, data enrichment, and asynchronous workloads" — which
  is exactly this pipeline.
- Priced at Batch API rates, **and prompt caching stacks on top**.
- In beta with limited model availability; the pricing page is the authority on which
  models support it.
- Two failure modes clients must handle:
  - **408 timeout.** Default client timeout is 10 minutes; the docs recommend raising it,
    and their samples use **15 minutes**. SDKs auto-retry a 408 twice.
  - **429 `resource_unavailable`.** Capacity shortfall. **You are not charged.** Retry
    with exponential backoff, or fall back to `service_tier: "auto"`.

Design consequences, both implemented: a 15-minute default timeout on flex calls, and an
automatic downgrade to `auto` after a configurable number of consecutive 429s.

## 7. What the v1.3 pipeline did, for reference

Read directly from `/nas4/data/workspace/curriculum` (git `dc69742`, 2026-04-12).

- Working store: `data/lexicon/<word>.json`, 205,996 files in one flat directory.
- Internal `LexemeEntry` carries per-node random UUIDs (`id`), timestamps, `metadata`,
  `tags`, `reading_level`, `alt_text`, `aligns_to_objective_ids`.
- `services/opengloss_export.py` flattens to the published record and **drops** edge and
  sense ids, per-node timestamps, and most per-node metadata. Only `lexeme_id` survives,
  and only because it equals the word slug.
- Therefore the published datasets cannot be rejoined to the working store at
  sense or edge granularity. FR-5.1 exists because of this.
