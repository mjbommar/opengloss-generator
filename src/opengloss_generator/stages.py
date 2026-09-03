"""The stage abstraction: one schema-validated model call, with retries and accounting.

A stage never returns a bare value. It returns a :class:`StageResult` carrying the output
*and* the usage, cost, attempt count, and provenance, because the caller is required to
record where content came from (FR-1.4).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, ModelHTTPError, NativeOutput, UnexpectedModelBehavior
from pydantic_ai.exceptions import ModelAPIError

from opengloss_generator.errors import StageFailedError
from opengloss_generator.log import get_logger
from opengloss_generator.pricing import ServiceTier, estimate_cost
from opengloss_generator.router import ModelRouter, estimate_tokens
from opengloss_generator.schema import Provenance, StageName

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from opengloss_generator.budget import BudgetGuard, CostMeter
    from opengloss_generator.config import AppConfig

__all__ = ["StageResult", "StageRunner"]

_LOG = get_logger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429
# 529 is Anthropic's "overloaded" status: transient, unbilled, and retried by the SDK only a
# few times. Left out, it cost the writer-diversity pilot 5-7 judged entries per arm (D-63).
_RETRYABLE_STATUS = frozenset({408, 409, _HTTP_TOO_MANY_REQUESTS, 500, 502, 503, 504, 529})
# Flex capacity rejections are unbilled and are the router's signal to downgrade the run
# to ``auto``. OpenAI has sent two shapes: the documented ``resource_unavailable`` body, and
# (observed 2026-09-02, sustained for over an hour on gpt-5.6-luna while ``default``
# answered in under a second) a ``rate_limit_exceeded`` 429 reading "We're currently
# processing too many requests". On the flex tier both mean the same thing.
_FLEX_UNAVAILABLE_MARKERS = (
    "resource_unavailable",
    "resource unavailable",
    "processing too many requests",
    "rate_limit_exceeded",
)
_FEEDBACK_MAX_CHARS = 1500


@dataclass(slots=True)
class StageResult[T: BaseModel]:
    """The output of one stage, with everything needed for provenance and accounting."""

    output: T
    provenance: Provenance
    cost_usd: float
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    attempts: int
    duration_seconds: float


class StageRunner:
    """Executes stages: builds the agent, retries, prices the call, records provenance.

    Args:
        config: Run configuration.
        router: Model and tier router.
        meter: Cost accumulator.
        guard: Budget guard.
        run_id: Identifier stamped onto provenance and ledger records.
        model_override: If given, every stage uses this model instead of the configured
            one. Tests pass a ``TestModel`` or ``FunctionModel`` here, which is what makes
            the whole pipeline runnable offline.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        router: ModelRouter,
        meter: CostMeter,
        guard: BudgetGuard,
        run_id: str,
        model_override: Model | None = None,
    ) -> None:
        """Store collaborators; no I/O happens here."""
        self._config = config
        self._router = router
        self._meter = meter
        self._guard = guard
        self._run_id = run_id
        self._model_override = model_override

    @property
    def config(self) -> AppConfig:
        """Return the run configuration this runner was built with."""
        return self._config

    async def run[T: BaseModel](
        self,
        *,
        stage: StageName,
        output_type: type[T],
        instructions: str,
        prompt: str,
        prompt_version: str = "1",
        writer_key: str | None = None,
    ) -> StageResult[T]:
        """Run a stage to a validated result.

        Args:
            stage: Which stage this is; selects the model policy and cache key.
            output_type: Pydantic model the response must validate against.
            instructions: Static system instructions. Kept first and byte-stable across
                calls so the provider's prompt cache can match on it.
            prompt: The volatile, per-call input. Always last.
            prompt_version: Version tag recorded in provenance.
            writer_key: A stable per-call identifier — a sense id, for the rendition
                and examples stages — used to draw a writer from the stage's policy
                (D-63) when it configures ``writers``. ``None`` (the default) always
                uses the policy's own ``model``, so every stage that does not opt into
                a writer rotation is unaffected.

        Returns:
            A :class:`StageResult`.

        Raises:
            StageFailedError: If every attempt failed.
            BudgetExceededError: If the run's ceiling was reached before dispatch.
        """
        policy = self._config.policy(stage)
        model_name = policy.writer_for(writer_key) if writer_key is not None else policy.model
        # NativeOutput with strict=True asks the provider for constrained decoding against
        # the JSON schema (non-strict JSON mode still omitted required fields live),
        # so enum and shape violations cannot occur at all (verified live 2026-09-02:
        # with tool-call output, gpt-5.4-nano filled `kind` with a part of speech on
        # every attempt). Only semantic validators can still fail, and those retry here.
        agent: Agent[None, T] = Agent(
            self._model_override or self._router.model_for(policy, model=model_name),
            output_type=NativeOutput(output_type, strict=True),
            instructions=instructions,
            retries=0,  # the retry loop lives here, so failures are priced and logged
        )
        started = time.monotonic()
        feedback: str | None = None
        last_error = "unknown"

        for attempt in range(1, policy.max_attempts + 1):
            tier = self._router.effective_tier(policy)
            body = prompt if feedback is None else f"{prompt}\n\n{feedback}"
            try:
                result = await self._attempt(
                    agent=agent,
                    stage=stage,
                    policy=policy,
                    tier=tier,
                    instructions=instructions,
                    body=body,
                    model_name=model_name,
                )
            except _RetryableAttemptError as exc:
                last_error = str(exc)
                feedback = exc.feedback
                _LOG.warning(
                    "stage_attempt_failed",
                    stage=stage.value,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    error=last_error,
                )
                if attempt < policy.max_attempts:
                    await self._backoff(attempt)
                continue

            self._router.note_success()
            return self._finalise(
                stage=stage,
                tier=tier,
                result=result,
                attempts=attempt,
                duration=time.monotonic() - started,
                prompt_version=prompt_version,
                model_name=model_name,
            )

        raise StageFailedError(stage.value, policy.max_attempts, last_error)

    async def _attempt(
        self,
        *,
        agent: Agent[None, Any],
        stage: StageName,
        policy: Any,  # noqa: ANN401 - ModelPolicy, imported only for typing
        tier: ServiceTier,
        instructions: str,
        body: str,
        model_name: str,
    ) -> Any:  # noqa: ANN401 - AgentRunResult is generic over the caller's output type
        """Make one governed model call.

        Reserves budget, waits on the rate limiter, calls the model, reconciles the
        token reservation, and translates provider and validation failures into
        :class:`_RetryableAttemptError`.

        Raises:
            _RetryableAttemptError: On any failure that another attempt might survive.
            StageFailedError: On a non-retryable provider error.
        """
        # Two different reservations, deliberately built from two different token
        # counts (D-41). The rate limiter protects TPM, where the provider enforces the
        # ceiling regardless of what is actually used, so it stays pessimistic and
        # reserves at `max_tokens` — over-reserving only costs throughput. The budget
        # guard protects a dollar ceiling against *actual* spend, so it reserves at
        # `expected_output_tokens`, a measured typical output; reserving every in-flight
        # call at `max_tokens` there was the bug (a RENDITIONS call measures ~250 output
        # tokens against an 8192 max_tokens ceiling, so 128 concurrent reservations held
        # roughly 30x their true cost and starved dispatch far below the budget).
        estimated_tokens = estimate_tokens(body, instructions, policy.max_tokens)
        estimated_usd = estimate_cost(
            model_name,
            input_tokens=estimated_tokens - policy.max_tokens,
            output_tokens=policy.expected_output_tokens,
            tier=tier,
        ).total_usd

        reservation = await self._guard.reserve(estimated_usd)
        limiter = self._router.limiter_for(policy, model=model_name)
        await limiter.acquire(estimated_tokens)
        try:
            result = await agent.run(
                body,
                model_settings=self._router.settings_for(policy, stage, model=model_name),
            )
        except ModelHTTPError as exc:
            self._note_http_failure(exc, tier)
            raise _RetryableAttemptError(f"http {exc.status_code}: {exc}", feedback=None) from exc
        except (ValidationError, UnexpectedModelBehavior) as exc:
            # pydantic-ai wraps the ValidationError in a generic "Exceeded maximum output
            # retries" message; the useful text is on the cause. Feed that back, or the
            # model is retried without ever being told what was wrong.
            detail = _validation_detail(exc)
            raise _RetryableAttemptError(
                f"invalid output: {detail}",
                feedback=(
                    "Your previous response did not satisfy the required schema. "
                    f"The validator reported: {detail}. "
                    "Return only a valid object for the schema."
                ),
            ) from exc
        except ModelAPIError as exc:
            raise _RetryableAttemptError(f"provider error: {exc}", feedback=None) from exc
        finally:
            await self._guard.release(reservation)

        usage = result.usage
        await limiter.reconcile(estimated_tokens, usage.input_tokens + usage.output_tokens)
        return result

    def _note_http_failure(self, exc: ModelHTTPError, tier: ServiceTier) -> None:
        """Classify an HTTP failure, tracking flex capacity rejections.

        Raises:
            StageFailedError: If the status is not retryable.
        """
        if exc.status_code not in _RETRYABLE_STATUS:
            raise StageFailedError("http", 1, f"non-retryable status {exc.status_code}: {exc}")
        if exc.status_code == _HTTP_TOO_MANY_REQUESTS and tier is ServiceTier.FLEX:
            text = str(exc.body or exc).lower()
            if any(marker in text for marker in _FLEX_UNAVAILABLE_MARKERS):
                # Capacity shortfall on flex is not billed; it is a scheduling problem.
                self._router.note_flex_rejection()

    def _finalise[T: BaseModel](
        self,
        *,
        stage: StageName,
        tier: ServiceTier,
        result: Any,  # noqa: ANN401 - AgentRunResult[T]
        attempts: int,
        duration: float,
        prompt_version: str,
        model_name: str,
    ) -> StageResult[T]:
        """Price a successful call and package it as a :class:`StageResult`."""
        usage = result.usage
        cached = getattr(usage, "cache_read_tokens", 0) or 0
        cost = self._meter.record(
            stage=stage.value,
            model=model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=cached,
            tier=tier,
        )
        # OpenRouter reports which upstream provider actually served the call (D-63);
        # every other provider leaves this unset rather than reporting itself, since
        # the model id already says which provider was asked.
        provider_details = getattr(result.response, "provider_details", None) or {}
        provider_served = provider_details.get("downstream_provider")
        provenance = Provenance(
            stage=stage,
            model=cost.model,
            provider=provider_served,
            prompt_version=prompt_version,
            service_tier=tier.value,
            input_tokens=usage.input_tokens,
            cached_input_tokens=cached,
            output_tokens=usage.output_tokens,
            cost_usd=cost.total_usd,
            attempts=attempts,
            run_id=self._run_id,
        )
        _LOG.info(
            "stage_complete",
            stage=stage.value,
            model=cost.model,
            provider=provider_served,
            tier=tier.value,
            attempts=attempts,
            input_tokens=usage.input_tokens,
            cached_input_tokens=cached,
            output_tokens=usage.output_tokens,
            cost_usd=round(cost.total_usd, 6),
            duration_seconds=round(duration, 3),
        )
        return StageResult(
            output=result.output,
            provenance=provenance,
            cost_usd=cost.total_usd,
            input_tokens=usage.input_tokens,
            cached_input_tokens=cached,
            output_tokens=usage.output_tokens,
            attempts=attempts,
            duration_seconds=duration,
        )

    async def _backoff(self, attempt: int) -> None:
        """Sleep for an exponentially growing, jittered interval."""
        base = self._config.concurrency.backoff_base_seconds
        ceiling = self._config.concurrency.backoff_max_seconds
        delay = min(base * (2 ** (attempt - 1)), ceiling)
        await asyncio.sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - jitter, not crypto


def _validation_detail(exc: BaseException) -> str:
    """Return the most specific validation message in an exception chain, truncated."""
    node: BaseException | None = exc
    while node is not None:
        if isinstance(node, ValidationError):
            return str(node)[:_FEEDBACK_MAX_CHARS]
        node = node.__cause__ or node.__context__
    return str(exc)[:_FEEDBACK_MAX_CHARS]


class _RetryableAttemptError(Exception):
    """Internal signal that an attempt failed in a way another attempt might survive.

    Attributes:
        feedback: Text appended to the next prompt. Set for schema failures, where
            telling the model what was wrong materially raises the success rate; left
            ``None`` for transport failures, where it would only waste tokens.
    """

    def __init__(self, message: str, *, feedback: str | None) -> None:
        """Record the message and the optional prompt feedback."""
        super().__init__(message)
        self.feedback = feedback
