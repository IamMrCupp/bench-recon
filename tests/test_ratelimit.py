"""The failed-login throttle.

A fake clock so windows are exercised without sleeping through them.
"""

from __future__ import annotations

from bench_recon.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_allows_up_to_the_limit_then_blocks() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_attempts=3, window_seconds=300, now=clock)

    for _ in range(3):
        assert limiter.allowed("1.2.3.4")
        limiter.record_failure("1.2.3.4")
    assert not limiter.allowed("1.2.3.4")


def test_success_clears_the_bucket() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_attempts=3, window_seconds=300, now=clock)
    for _ in range(3):
        limiter.record_failure("1.2.3.4")
    assert not limiter.allowed("1.2.3.4")

    limiter.record_success("1.2.3.4")
    assert limiter.allowed("1.2.3.4")


def test_window_rolls_off() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_attempts=3, window_seconds=300, now=clock)
    for _ in range(3):
        limiter.record_failure("1.2.3.4")
    assert not limiter.allowed("1.2.3.4")

    clock.advance(301)  # the whole window has passed
    assert limiter.allowed("1.2.3.4")


def test_retry_after_counts_down() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_attempts=2, window_seconds=300, now=clock)
    limiter.record_failure("c")
    limiter.record_failure("c")
    assert limiter.retry_after("c") == 300
    clock.advance(120)
    assert limiter.retry_after("c") == 180


def test_buckets_are_evicted_so_a_scanner_cant_leak_memory() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_attempts=5, window_seconds=300, now=clock)
    for i in range(50):
        limiter.record_failure(f"10.0.0.{i}")
    assert limiter.tracked_clients() == 50

    clock.advance(301)
    limiter.allowed("probe")  # any call triggers eviction
    assert limiter.tracked_clients() == 0


def test_unknown_clients_share_one_bucket() -> None:
    limiter = RateLimiter(max_attempts=2)
    limiter.record_failure(None)
    limiter.record_failure(None)
    # Failing closed: an unidentifiable client is throttled, not given a pass.
    assert not limiter.allowed(None)
