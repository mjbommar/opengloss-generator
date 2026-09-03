"""Layered, validated configuration.

Precedence, lowest to highest: built-in defaults, a TOML config file, ``OPENGLOSS_*``
environment variables, then explicit CLI flags. The whole thing is a Pydantic model, so
an invalid configuration fails at startup with a readable error and before any spend.

Secrets are read from the provider's own environment variables (``OPENAI_API_KEY``,
``ANTHROPIC_API_KEY``) by the provider SDKs. They are deliberately absent from this model
so they cannot be serialised into a log line or a run manifest.
"""

from __future__ import annotations

import itertools
import tomllib
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opengloss_generator.pricing import ServiceTier, known_models
from opengloss_generator.schema import ReadingLevel, Register, StageName

__all__ = [
    "DEFAULT_ENCYCLOPEDIA_TARGETS",
    "DEFAULT_EXAMPLE_REGISTERS",
    "DEFAULT_READING_LEVELS",
    "DEFAULT_REGISTERS",
    "MAX_EXAMPLES_PER_SENSE",
    "AppConfig",
    "ConcurrencyConfig",
    "ExamplesConfig",
    "ModelPolicy",
    "ReadabilityConfig",
    "StoreConfig",
    "load_config",
]

DEFAULT_READING_LEVELS: tuple[ReadingLevel, ...] = (
    ReadingLevel.GRADE_1,
    ReadingLevel.GRADE_5,
    ReadingLevel.GRADE_10,
    ReadingLevel.COLLEGE,
)
DEFAULT_REGISTERS: tuple[Register, ...] = (
    Register.INFORMAL,
    Register.FORMAL,
    Register.TECHNICAL,
    Register.MARKETING,
)
DEFAULT_ENCYCLOPEDIA_TARGETS: tuple[tuple[ReadingLevel, Register], ...] = tuple(
    (level, Register.PLAIN) for level in DEFAULT_READING_LEVELS
)
#: The register axis of the example-sentence workflow (D-53). ``slang`` rather than
#: ``marketing``: an example sentence is something a person says, and a slang sentence is
#: a real thing a person says about a river bank or a bank account, whereas a marketing
#: sentence about one is an advertisement, not a use of the word.
DEFAULT_EXAMPLE_REGISTERS: tuple[Register, ...] = (
    Register.INFORMAL,
    Register.FORMAL,
    Register.TECHNICAL,
    Register.SLANG,
)
#: Ceiling on ``ExamplesConfig.per_sense``. One call writes this many sentences for every
#: live sense of an entry at once, so the product is what has to fit inside the stage's
#: ``max_tokens``; twenty-five per sense is far past anything useful and is here only so a
#: mistyped configuration fails at startup instead of truncating a live answer.
MAX_EXAMPLES_PER_SENSE = 25


class ModelPolicy(BaseModel):
    """Which model, tier, and effort a single stage uses."""

    model_config = ConfigDict(extra="forbid")

    model: str
    service_tier: ServiceTier = ServiceTier.FLEX
    reasoning_effort: str | None = "low"
    temperature: float | None = None
    max_tokens: int = 4096
    # Budget *reservation* uses this, not `max_tokens` (D-41): `max_tokens` is a ceiling
    # a call almost never reaches, so reserving at it against a high-concurrency sweep
    # holds phantom budget that starves dispatch long before the ceiling is actually
    # spent. This is a measured, per-stage typical output token count instead.
    expected_output_tokens: int = 512
    timeout_seconds: float = 900.0
    max_attempts: int = 3

    @model_validator(mode="after")
    def _model_must_be_priced(self) -> Self:
        """Refuse a model with no price row, so no run can report a false $0."""
        bare = self.model.split(":", 1)[-1]
        if bare not in known_models():
            raise ValueError(
                f"model {bare!r} has no entry in pricing.PRICE_TABLE; "
                "add its rates before selecting it"
            )
        return self

    @model_validator(mode="after")
    def _expected_output_within_ceiling(self) -> Self:
        """Refuse an `expected_output_tokens` that exceeds the call's own ceiling."""
        if self.expected_output_tokens > self.max_tokens:
            raise ValueError(
                f"expected_output_tokens ({self.expected_output_tokens}) exceeds "
                f"max_tokens ({self.max_tokens})"
            )
        return self


def _default_policies() -> dict[StageName, ModelPolicy]:
    """Return the per-stage model defaults.

    No policy sets ``temperature``: gpt-5.x reasoning models reject sampling parameters
    when reasoning is enabled (verified live 2026-09-02 — the request fails with
    "Sampling parameters ['temperature'] are not supported when reasoning is enabled").
    Stylistic diversity for prose comes from the prompt, not from sampling.

    ``max_tokens`` on the Responses API includes reasoning tokens. A four-sense set at
    medium effort truncated at 4096 live (JSON cut off at character 4505), so prose
    stages get 8192; the marginal cost is only paid when used.

    Structural stages get the cheap nano model; the quality-critical sense stage and the
    long-form stages get ``gpt-5.6-luna``, the cheapest current-generation model. QA runs
    on a different model family from the generator so it is not marking its own homework.
    """
    luna = "gpt-5.6-luna"
    nano = "gpt-5.4-nano"
    # `expected_output_tokens` below are measured, not modelled (docs/COST-MODEL.md,
    # docs/CORE-DIARY.md Iteration 4): renditions ~250, resolve ~36 on the fixed prompt,
    # the other nano classification stages ~30-60, senses ~400-570, encyclopedia
    # ~350-1,600, etymology ~300-400. Each default below rounds that measurement up to
    # a safety margin, still far under `max_tokens` (D-41).
    return {
        StageName.OVERVIEW: ModelPolicy(
            model=nano, reasoning_effort="low", max_tokens=2048, expected_output_tokens=200
        ),
        StageName.SENSES: ModelPolicy(
            model=luna, reasoning_effort="medium", max_tokens=8192, expected_output_tokens=600
        ),
        # Rewriting, with the source text supplied: prose, so luna, but low effort. The
        # encyclopedia shares this policy and is the expensive case (output is roughly
        # N x 350 words), which is why its default target set is levels only. Its
        # instructions are deliberately ~1.7K tokens: OpenAI only caches a prefix of
        # 1,024 tokens or more, and the iteration-1 pilot got zero cache hits on 177K
        # input tokens because the prefix was ~350. A readability retry (below) re-sends
        # the same prefix, so its input is cached at a tenth of the price.
        StageName.RENDITIONS: ModelPolicy(
            model=luna, reasoning_effort="low", max_tokens=8192, expected_output_tokens=400
        ),
        # One call per entry writes every sense's whole set of fresh example sentences
        # (D-53). Prose for a reader, so luna, but low effort: a natural sentence using a
        # given sense of a word is not a reasoning problem, and the acceptance rules that
        # follow are deterministic, so thinking harder about the wording buys nothing the
        # checks can see. `expected_output_tokens` is measured, not modelled (D-41): the
        # three live core entries of D-53's check measured 296, 994 and 1,804 output
        # tokens at 1, 2 and 7 live senses. Unlike every other stage's, this one's output
        # scales with the entry rather than sitting around a mean, so the reservation is
        # set above the measured mean (~1,050) at a typical multi-sense entry's cost.
        StageName.EXAMPLES: ModelPolicy(
            model=luna, reasoning_effort="low", max_tokens=8192, expected_output_tokens=1200
        ),
        # The examples sense-fit verdict is a handful of integers; at "low" the hidden
        # reasoning tokens were half the cost of a many-sense entry (D-53). Same lever as D-38.
        StageName.SENSE_CHECK: ModelPolicy(
            model=nano, reasoning_effort="none", max_tokens=2048, expected_output_tokens=100
        ),
        StageName.ETYMOLOGY: ModelPolicy(
            model=luna, reasoning_effort="medium", max_tokens=2048, expected_output_tokens=400
        ),
        StageName.ENCYCLOPEDIA: ModelPolicy(
            model=luna, reasoning_effort="medium", max_tokens=8192, expected_output_tokens=1600
        ),
        StageName.LEXICAL_EXPLANATION: ModelPolicy(
            model=luna, reasoning_effort="low", max_tokens=1024, expected_output_tokens=150
        ),
        # These five structural passes write no prose: their output is an enum, an
        # integer, a float, or a pair of offsets, so `"none"` (reasoning off entirely,
        # not just `"low"`) is the whole decision (docs/SCHEMA-V3.md 5) — `nano`
        # supports it (`openai_supports_reasoning_effort_none`, verified live
        # 2026-09-02 via OpenAIResponsesModelSettings). A live resolve run at `"low"`
        # measured ~660 output tokens/call for a 3-field answer (docs/COST-MODEL.md,
        # resolve row); reasoning tokens count as output and are billed but never seen,
        # so this is the single biggest per-call lever these stages have. HYGIENE keeps
        # `"low"`: it edits and rewrites gloss text, closer to the prose stages above.
        StageName.CLASSIFY_KIND: ModelPolicy(
            model=nano, reasoning_effort="none", max_tokens=2048, expected_output_tokens=100
        ),
        StageName.HYGIENE: ModelPolicy(
            model=nano, reasoning_effort="low", max_tokens=2048, expected_output_tokens=300
        ),
        StageName.TAG_DOMAIN: ModelPolicy(
            model=nano, reasoning_effort="none", max_tokens=2048, expected_output_tokens=100
        ),
        StageName.RESOLVE: ModelPolicy(
            model=nano, reasoning_effort="none", max_tokens=2048, expected_output_tokens=100
        ),
        StageName.SPANS: ModelPolicy(
            model=nano, reasoning_effort="none", max_tokens=2048, expected_output_tokens=100
        ),
        StageName.FRONTIER: ModelPolicy(
            model=nano, reasoning_effort="none", max_tokens=2048, expected_output_tokens=200
        ),
        StageName.QA: ModelPolicy(
            model="claude-opus-5",
            service_tier=ServiceTier.DEFAULT,
            reasoning_effort=None,
            # A many-sense verdict truncated at the default ceiling on 2 of 60 entries
            # (QA-DIARY iteration 1); the judge writes ~3K tokens of structured output.
            max_tokens=8192,
            expected_output_tokens=800,
        ),
    }


class ConcurrencyConfig(BaseModel):
    """Worker pool and provider throughput limits."""

    model_config = ConfigDict(extra="forbid")

    workers: int = Field(default=8, ge=1, le=256)
    requests_per_minute: int = Field(default=480, ge=1)
    tokens_per_minute: int = Field(default=2_000_000, ge=1000)
    max_flex_429s: int = Field(
        default=3,
        ge=1,
        description="Consecutive flex capacity rejections before downgrading to auto.",
    )
    backoff_base_seconds: float = Field(default=1.0, gt=0)
    backoff_max_seconds: float = Field(default=60.0, gt=0)


class ReadabilityConfig(BaseModel):
    """How hard the renditions workflow checks that a rewrite came out as asked.

    Telling a model what "grade 1" means is not the same as it happening: the first core
    pilot produced a grade-1 encyclopedia entry containing ``m/s^2``. Every rendition is
    therefore scored with :func:`~opengloss_generator.readability.flesch_kincaid_grade`
    and the score is stored on its ``Assessment``. Only the two lowest levels are
    regenerated on a miss, and only once: the higher bands are wide, and a second retry
    costs more than the improvement is worth.

    The same block carries the second generation-time check on a rendition, for the same
    reason and with the same shape: a gloss rendition that begins by naming its own
    headword is a miss too (:func:`~opengloss_generator.hygiene.is_headword_initial`,
    D-39). A target failing both checks is retried once, with both pieces of feedback.

    It carries the vocabulary check on the same terms (D-51). Flesch-Kincaid measures
    sentence and syllable length, not whether a reader knows the words: a judged sample
    found 46.6% of grade_1 encyclopedia renditions not level-appropriate although every
    one of them passed its FK band ("Monks made vows of poverty, chastity, and
    obedience." is a nine-word sentence of short words). So the share of a rendition's
    words that are not on the familiar-word list
    (:func:`~opengloss_generator.vocabulary.hard_word_share`) is measured on every
    rendition and acted on at ``grade_1`` and ``grade_5``, through the same single retry.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    headword_initial_retry: bool = Field(
        default=True,
        description=(
            "Whether a gloss rendition that begins by naming its own headword is "
            "re-requested once and, if it still does, flagged og.headword_initial. "
            "Independent of `enabled`, which governs the readability band check."
        ),
    )
    headword_absent_retry: bool = Field(
        default=True,
        description=(
            "Whether an example rendition that contains no form of its own headword at "
            "all is re-requested once and, if it still does not, flagged "
            "og.headword_absent (D-45). Independent of `enabled`."
        ),
    )
    vocabulary_check: bool = Field(
        default=True,
        description=(
            "Whether a grade_1 or grade_5 rendition carrying too many words that are not "
            "on the Dale-Chall familiar-word list is re-requested once and, if it still "
            "does, flagged og.hard_vocabulary (D-51). Independent of `enabled`, which "
            "governs the Flesch-Kincaid band check: the two measure different things, "
            "and 46.6% of judged grade_1 encyclopedia renditions passed the second while "
            "failing the first (docs/QA-DIARY.md)."
        ),
    )
    near_copy_retry: bool = Field(
        default=True,
        description=(
            "Whether a non-plain-register gloss rendition whose content-word set is at "
            "least 90% the same as the canonical gloss's "
            "(:func:`~opengloss_generator.hygiene.is_near_copy`) is re-requested once and, "
            "if it still is, flagged og_near_copy (D-59). Independent of `enabled`."
        ),
    )
    vocabulary_tolerance: float = Field(
        default=0.05,
        ge=0.0,
        description=(
            "How far above its level's unfamiliar-word share a rendition may measure "
            "before it is regenerated. Absorbs the gaps in a 1948 word list, which has "
            "no entry for 'serious', 'problem' or 'area'."
        ),
    )
    tolerance: float = Field(
        default=1.5,
        ge=0.0,
        description=(
            "How far above its band's upper bound a rendition may measure before it is "
            "regenerated. Absorbs the noise in a heuristic syllable count."
        ),
    )
    retry_levels: list[ReadingLevel] = Field(
        default_factory=lambda: [ReadingLevel.GRADE_1, ReadingLevel.GRADE_5],
        description="Reading levels whose renditions are regenerated once when they miss.",
    )


class ExamplesConfig(BaseModel):
    """How many verified example sentences a sense gets, and at which targets (D-53).

    The QA judge's two standing complaints about examples are that they do not read like
    something a person would say (``examples_natural``, 29.6% then 33.3% of senses — the
    stilted "Researchers formed a duo…" register) and that they illustrate a sibling sense
    rather than the one they are filed under (``examples_fit_sense``, 34.1% then 31.8%),
    and ``docs/QA-DIARY.md`` iteration 4 concluded that neither is repairable by a pattern
    rewrite: "rewriting by pattern moves the pattern". The answer is generation, in volume,
    with every sentence checked before it is kept.

    Two axes, and unlike a
    :class:`~opengloss_generator.workflows.enrich.RenditionRequest` they are **not**
    crossed. Crossing four levels with four registers asks for a grade_1 technical example
    sentence, which is not a thing. Instead each level is paired with ``plain`` and each
    register with ``neutral``, and the resulting list is cycled to fill ``per_sense``
    slots, so the default eight are exactly ``grade_1/plain``, ``grade_5/plain``,
    ``grade_10/plain``, ``college/plain``, ``neutral/informal``, ``neutral/formal``,
    ``neutral/technical`` and ``neutral/slang`` — one sense's sentences span audiences
    instead of repeating one.
    """

    model_config = ConfigDict(extra="forbid")

    per_sense: int = Field(default=8, ge=1, le=MAX_EXAMPLES_PER_SENSE)
    registers: list[Register] = Field(default_factory=lambda: list(DEFAULT_EXAMPLE_REGISTERS))
    reading_levels: list[ReadingLevel] = Field(default_factory=lambda: list(DEFAULT_READING_LEVELS))
    min_words: int = Field(
        default=6,
        ge=1,
        description="Fewest words an accepted sentence may have; below it is a fragment.",
    )
    max_words: int = Field(
        default=22,
        ge=1,
        description=(
            "Most words an accepted sentence may have at any level. The two lowest levels "
            "are capped tighter still, by the same numbers RENDITIONS_INSTRUCTIONS states."
        ),
    )
    sense_check: bool = Field(
        default=True,
        description=(
            "Whether a second, cheap call asks which listed sense each accepted sentence "
            "actually illustrates, dropping the ones written for a sense they do not fit. "
            "This is the one acceptance rule no deterministic check can stand in for, and "
            "it is the judge's largest measured example defect (D-53)."
        ),
    )

    @model_validator(mode="after")
    def _word_band_is_ordered(self) -> Self:
        """Refuse a word band whose floor is above its ceiling."""
        if self.min_words > self.max_words:
            raise ValueError(f"min_words ({self.min_words}) exceeds max_words ({self.max_words})")
        return self

    def targets(self) -> list[tuple[ReadingLevel, Register]]:
        """Return the ``(reading_level, register)`` target of each of a sense's sentences.

        Returns:
            Exactly :attr:`per_sense` targets, in the order the model is asked for them:
            each configured reading level at ``plain``, then ``neutral`` at each
            configured register, cycled if ``per_sense`` outruns the list. An empty
            configuration of both axes yields the canonical ``(neutral, plain)`` target,
            so the workflow always has somewhere to put a sentence.
        """
        base = [(level, Register.PLAIN) for level in self.reading_levels]
        base += [(ReadingLevel.NEUTRAL, register) for register in self.registers]
        if not base:
            base = [(ReadingLevel.NEUTRAL, Register.PLAIN)]
        return list(itertools.islice(itertools.cycle(base), self.per_sense))


class StoreConfig(BaseModel):
    """Where entries live and how they are locked."""

    model_config = ConfigDict(extra="forbid")

    root: Path = Path("data/store")
    # A rendition worker holds an entry lock across its model calls (D-31), which can
    # run past two minutes for the encyclopedia field at flex latency. A concurrent
    # pass that waits only 30 s skips those entries (90 of 10,000 in the core repair
    # run, 2026-09-02). Waiting is cheaper than a retry pass.
    lock_timeout_seconds: float = Field(default=300.0, gt=0)
    lock_stale_seconds: float = Field(default=900.0, gt=0)
    fsync_on_write: bool = True


class AppConfig(BaseSettings):
    """Top-level configuration for a run."""

    model_config = SettingsConfigDict(
        env_prefix="OPENGLOSS_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    language: str = "en"
    store: StoreConfig = Field(default_factory=StoreConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    readability: ReadabilityConfig = Field(default_factory=ReadabilityConfig)
    examples: ExamplesConfig = Field(default_factory=ExamplesConfig)
    policies: dict[StageName, ModelPolicy] = Field(default_factory=_default_policies)

    budget_usd: float | None = Field(default=10.0, gt=0)
    dry_run: bool = False
    fail_fast: bool = False

    default_reading_levels: list[ReadingLevel] = Field(
        default_factory=lambda: list(DEFAULT_READING_LEVELS)
    )
    default_registers: list[Register] = Field(default_factory=lambda: list(DEFAULT_REGISTERS))

    encyclopedia_rendition_targets: list[tuple[ReadingLevel, Register]] = Field(
        default_factory=lambda: list(DEFAULT_ENCYCLOPEDIA_TARGETS),
        description=(
            "Rendition targets for the entry-level encyclopedia section. Reading levels "
            "crossed with plain only: the encyclopedia is the one field whose output is "
            "the length of its input, so a register axis would multiply a 350-word "
            "generation by five for a section nobody reads in a marketing voice."
        ),
    )

    batch_threshold: int = Field(
        default=1000,
        ge=1,
        description="Item count above which a sweep should use the provider Batch API.",
    )

    log_dir: Path = Path("runs")
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _every_stage_has_a_policy(self) -> Self:
        """Require a policy for every stage, so no stage silently falls back."""
        missing = sorted(s.value for s in StageName if s not in self.policies)
        if missing:
            raise ValueError(f"no model policy configured for stage(s): {missing}")
        return self

    def policy(self, stage: StageName) -> ModelPolicy:
        """Return the model policy for a stage."""
        return self.policies[stage]


def load_config(path: Path | None = None, **overrides: Any) -> AppConfig:  # noqa: ANN401
    """Build an :class:`AppConfig` from file, environment, and explicit overrides.

    Args:
        path: Optional TOML file. Its top-level keys map onto ``AppConfig`` fields.
        **overrides: Values taking precedence over both file and environment,
            typically parsed CLI flags. ``None`` values are dropped so an unset flag
            does not clobber a configured value.

    Returns:
        A validated configuration.

    Raises:
        FileNotFoundError: If ``path`` is given and does not exist.
    """
    data: dict[str, Any] = {}
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        data.update(tomllib.loads(path.read_text(encoding="utf-8")))
    data.update({k: v for k, v in overrides.items() if v is not None})
    return AppConfig(**data)
