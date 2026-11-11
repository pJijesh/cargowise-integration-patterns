"""Token-bucket rate limiter used to protect the downstream ERP."""

from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    """Allows `rate` calls per second with a burst of `burst`. A rate of 0 disables it."""

    def __init__(
        self,
        rate: float,
        burst: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = rate
        self.burst = max(1, burst)
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(self.burst)
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a token is available. Returns the seconds waited."""
        if self.rate <= 0:
            return 0.0
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                needed = (1.0 - self._tokens) / self.rate
            self._sleep(needed)
            waited += needed
