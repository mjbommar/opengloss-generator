"""Token-bucket rate limiting for request and token throughput.

The provider rejects on requests-per-minute and tokens-per-minute, so those are the two
dimensions we govern. In-flight request *count* is handled separately by the worker pool
and by pydantic-ai's own concurrency limiter.

Token spend must be reserved before a call, when only an estimate is available. We
deliberately over-estimate and reconcile against reported usage afterwards: over-estimating
costs throughput, under-estimating costs 429s.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

__all__ = ["RateLimiter", "TokenBucket"]


@dataclass
class TokenBucket:
    """A monotonic-clock token bucket.

    Attributes:
        capacity: Maximum tokens the bucket holds, i.e. the per-minute allowance.
        refill_per_second: Tokens added per second.
    """

    capacity: float
    refill_per_second: float
    _tokens: float = field(init=False)
    _updated: float = field(init=False)

    def __post_init__(self) -> None:
        """Start the bucket full."""
        if self.capacity <= 0 or self.refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        self._tokens = self.capacity
        self._updated = time.monotonic()

    def _refill(self) -> None:
        """Add the tokens accrued since the last observation."""
        now = time.monotonic()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)

    def try_consume(self, amount: float) -> bool:
        """Consume ``amount`` tokens if available.

        Args:
            amount: Tokens to consume.

        Returns:
            ``True`` if the tokens were consumed, ``False`` if the bucket is short.
        """
        self._refill()
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False

    def wait_time(self, amount: float) -> float:
        """Return the seconds until ``amount`` tokens would be available."""
        self._refill()
        if self._tokens >= amount:
            return 0.0
        # A request larger than the bucket can never be satisfied by waiting; clamp it
        # so the caller waits one full refill rather than forever.
        needed = min(amount, self.capacity) - self._tokens
        return max(0.0, needed / self.refill_per_second)

    def refund(self, amount: float) -> None:
        """Return unused tokens to the bucket, never exceeding capacity."""
        self._refill()
        self._tokens = min(self.capacity, self._tokens + amount)


class RateLimiter:
    """Governs requests-per-minute and tokens-per-minute for one model.

    Args:
        requests_per_minute: Request allowance.
        tokens_per_minute: Token allowance.
    """

    def __init__(self, requests_per_minute: int, tokens_per_minute: int) -> None:
        """Build the two buckets and the mutex that serialises admission."""
        self._requests = TokenBucket(requests_per_minute, requests_per_minute / 60.0)
        self._tokens = TokenBucket(tokens_per_minute, tokens_per_minute / 60.0)
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int) -> None:
        """Block until one request and ``estimated_tokens`` tokens are available.

        Args:
            estimated_tokens: Conservative upper bound on the call's total tokens.
        """
        while True:
            async with self._lock:
                delay = max(
                    self._requests.wait_time(1),
                    self._tokens.wait_time(estimated_tokens),
                )
                if delay <= 0:
                    self._requests.try_consume(1)
                    self._tokens.try_consume(estimated_tokens)
                    return
            await asyncio.sleep(delay)

    async def reconcile(self, estimated_tokens: int, actual_tokens: int) -> None:
        """Refund an over-estimate, or debit an under-estimate, after a call returns.

        Args:
            estimated_tokens: What :meth:`acquire` reserved.
            actual_tokens: What the provider reported.
        """
        delta = estimated_tokens - actual_tokens
        async with self._lock:
            if delta > 0:
                self._tokens.refund(delta)
            elif delta < 0:
                self._tokens.try_consume(-delta)
