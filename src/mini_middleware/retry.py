"""Retry with exponential backoff and full jitter."""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from .delivery import PermanentDeliveryError, TransientDeliveryError

T = TypeVar("T")


class RetriesExhausted(Exception):
    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"gave up after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random) -> float:
    """Full jitter: a random delay between 0 and min(cap, base * 2^(attempt-1)).

    Randomising the whole interval, not just a small fraction, spreads out retries from
    many messages that failed at the same moment, which avoids synchronised retry storms.
    """
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return rng.uniform(0.0, ceiling)


class RetryPolicy:
    def __init__(
        self,
        max_attempts: int = 4,
        base: float = 0.5,
        cap: float = 30.0,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.base = base
        self.cap = cap
        self.rng = rng or random.Random()
        self.sleep = sleep

    def run(
        self,
        fn: Callable[[int], T],
        on_retry: Callable[[int, float, Exception], None] | None = None,
    ) -> tuple[T, int]:
        """Call `fn(attempt)` until it succeeds. Returns (result, attempts used).

        Permanent errors propagate immediately with `.attempts` set. Transient errors are
        retried; when attempts run out `RetriesExhausted` is raised.
        """
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn(attempt), attempt
            except PermanentDeliveryError as exc:
                exc.attempts = attempt
                raise
            except TransientDeliveryError as exc:
                if attempt >= self.max_attempts:
                    raise RetriesExhausted(attempt, exc) from exc
                delay = backoff_delay(attempt, self.base, self.cap, self.rng)
                if exc.retry_after is not None:
                    delay = max(delay, min(exc.retry_after, self.cap))
                if on_retry:
                    on_retry(attempt, delay, exc)
                self.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover
