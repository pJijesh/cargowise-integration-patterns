"""HTTP delivery to the (mock) ERP, with an explicit transient / permanent error taxonomy."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from enum import Enum

from .ratelimit import RateLimiter


class DeliveryOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"  # ERP reported a duplicate: treated as success


class TransientDeliveryError(Exception):
    """Worth retrying: timeouts, connection failures, 5xx, 408, 429."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentDeliveryError(Exception):
    """Not worth retrying: the ERP will never accept this message as sent."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.attempts = 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A POST that is silently turned into a GET by a redirect would be a nasty surprise."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


def _retry_after(headers) -> float | None:
    raw = headers.get("Retry-After") if headers else None
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None  # HTTP-date form is not handled in this sketch


class ErpClient:
    def __init__(
        self,
        url: str,
        token: str = "",
        timeout: float = 5.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.url = url
        self._token = token
        self.timeout = timeout
        self._limiter = limiter
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def send(self, body: bytes, idempotency_key: str, correlation_id: str) -> DeliveryOutcome:
        if self._limiter:
            self._limiter.acquire()
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers=self._headers(
                {
                    "Content-Type": "application/xml",
                    "Idempotency-Key": idempotency_key,
                    "X-Correlation-Id": correlation_id,
                }
            ),
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                resp.read()
                return DeliveryOutcome.ACCEPTED
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 409:
                return DeliveryOutcome.ALREADY_PROCESSED
            if status in (408, 429) or status >= 500:
                raise TransientDeliveryError(
                    f"ERP returned HTTP {status}", retry_after=_retry_after(exc.headers)
                ) from exc
            detail = exc.read(300).decode("utf-8", "replace")
            raise PermanentDeliveryError(f"ERP returned HTTP {status}: {detail}", status) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
            raise TransientDeliveryError(f"{type(exc).__name__}: {exc}") from exc

    def fetch_records(self) -> list[dict]:
        """Read back what the ERP says it holds. Used only by reconciliation."""
        req = urllib.request.Request(self.url, headers=self._headers(), method="GET")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())["records"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            raise RuntimeError(f"could not read ERP records: {exc}") from exc
