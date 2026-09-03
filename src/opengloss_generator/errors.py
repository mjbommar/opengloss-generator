"""Exception hierarchy.

Every error raised by this package derives from :class:`OpenGlossError`, so a caller can
distinguish our failures from provider or stdlib failures without string matching.
"""

from __future__ import annotations

__all__ = [
    "BudgetExceededError",
    "GenerationError",
    "LockTimeoutError",
    "OpenGlossError",
    "StageFailedError",
    "StoreError",
]


class OpenGlossError(Exception):
    """Base class for every error raised by this package."""


class GenerationError(OpenGlossError):
    """A generation workflow could not produce a valid entry."""


class StageFailedError(GenerationError):
    """A single generation stage exhausted its retries.

    Attributes:
        stage: Name of the stage that failed.
        attempts: How many attempts were made.
    """

    def __init__(self, stage: str, attempts: int, message: str) -> None:
        """Record the failing stage alongside the underlying message."""
        super().__init__(f"stage {stage!r} failed after {attempts} attempt(s): {message}")
        self.stage = stage
        self.attempts = attempts


class BudgetExceededError(OpenGlossError):
    """The run's budget ceiling was reached.

    Attributes:
        budget_usd: The configured ceiling.
        committed_usd: Spend already committed when the ceiling was hit.
    """

    def __init__(self, budget_usd: float, committed_usd: float) -> None:
        """Record the ceiling and the spend that reached it."""
        super().__init__(f"budget of ${budget_usd:.4f} reached (committed ${committed_usd:.4f})")
        self.budget_usd = budget_usd
        self.committed_usd = committed_usd


class StoreError(OpenGlossError):
    """The content store could not complete an operation."""


class LockTimeoutError(StoreError):
    """An entry lock could not be acquired within the configured timeout."""
