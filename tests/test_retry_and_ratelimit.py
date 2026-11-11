import random

import pytest

from mini_middleware.delivery import PermanentDeliveryError, TransientDeliveryError
from mini_middleware.ratelimit import RateLimiter
from mini_middleware.retry import RetriesExhausted, RetryPolicy, backoff_delay


def test_backoff_ceiling_doubles_and_is_capped():
    rng = random.Random(0)
    for attempt, ceiling in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 10.0), (9, 10.0)]:
        samples = [backoff_delay(attempt, base=1.0, cap=10.0, rng=rng) for _ in range(200)]
        assert 0.0 <= min(samples)
        assert max(samples) <= ceiling
        assert max(samples) > ceiling * 0.8  # jitter really spans the interval


def test_backoff_is_deterministic_with_a_seeded_rng():
    a = [backoff_delay(n, 0.5, 30, random.Random(42)) for n in range(1, 5)]
    b = [backoff_delay(n, 0.5, 30, random.Random(42)) for n in range(1, 5)]
    assert a == b


def test_transient_errors_are_retried_until_success():
    sleeps, calls = [], []

    def fn(n):
        calls.append(n)
        if n < 3:
            raise TransientDeliveryError("boom")
        return "ok"

    policy = RetryPolicy(max_attempts=5, base=0.1, cap=1, rng=random.Random(1), sleep=sleeps.append)
    assert policy.run(fn) == ("ok", 3)
    assert calls == [1, 2, 3] and len(sleeps) == 2


def test_gives_up_after_max_attempts():
    policy = RetryPolicy(max_attempts=3, base=0.1, cap=1, sleep=lambda s: None)
    with pytest.raises(RetriesExhausted) as exc:
        policy.run(lambda n: (_ for _ in ()).throw(TransientDeliveryError("down")))
    assert exc.value.attempts == 3


def test_permanent_errors_are_not_retried():
    sleeps, calls = [], []

    def fn(n):
        calls.append(n)
        raise PermanentDeliveryError("no", 422)

    policy = RetryPolicy(max_attempts=5, sleep=sleeps.append)
    with pytest.raises(PermanentDeliveryError) as exc:
        policy.run(fn)
    assert calls == [1] and sleeps == [] and exc.value.attempts == 1


def test_retry_after_is_a_lower_bound_for_the_delay():
    sleeps = []

    def fn(n):
        if n == 1:
            raise TransientDeliveryError("throttled", retry_after=2.0)
        return "ok"

    RetryPolicy(max_attempts=3, base=0.01, cap=30, sleep=sleeps.append).run(fn)
    assert sleeps[0] >= 2.0


def test_rate_limiter_spaces_calls():
    now = [0.0]
    waits = []

    def sleep(s):
        waits.append(s)
        now[0] += s

    limiter = RateLimiter(rate=2.0, burst=1, clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        limiter.acquire()
    assert waits == pytest.approx([0.5, 0.5, 0.5])


def test_rate_limiter_disabled_at_zero():
    assert RateLimiter(0).acquire() == 0.0
