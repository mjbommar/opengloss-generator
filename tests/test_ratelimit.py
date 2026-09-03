"""Token bucket behaviour."""

from __future__ import annotations

import pytest

from opengloss_generator.ratelimit import RateLimiter, TokenBucket


def test_bucket_starts_full_and_drains():
    bucket = TokenBucket(capacity=10, refill_per_second=10)
    assert bucket.try_consume(10)
    assert not bucket.try_consume(1)
    assert bucket.wait_time(1) > 0


def test_refund_is_capped_at_capacity():
    bucket = TokenBucket(capacity=10, refill_per_second=1)
    bucket.try_consume(10)
    bucket.refund(1000)
    assert bucket.try_consume(10)


def test_oversized_request_does_not_wait_forever():
    bucket = TokenBucket(capacity=10, refill_per_second=10)
    bucket.try_consume(10)
    # A request bigger than the bucket is clamped to one full refill, not infinity.
    assert bucket.wait_time(1_000_000) <= 1.0


def test_rejects_nonsense_configuration():
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(capacity=0, refill_per_second=1)


async def test_limiter_admits_within_allowance():
    limiter = RateLimiter(requests_per_minute=600, tokens_per_minute=600_000)
    await limiter.acquire(1000)
    await limiter.reconcile(1000, 400)
