"""The processing pipeline: validate, deduplicate, order-check, transform, deliver, record."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from enum import Enum

from . import dlq as dlq_mod
from .config import Config
from .delivery import ErpClient, PermanentDeliveryError
from .dlq import DeadLetterStore
from .idempotency import BeginStatus, IdempotencyStore
from .logging_setup import correlation, log_event, new_correlation_id
from .metrics import METRICS, Metrics
from .ratelimit import RateLimiter
from .retry import RetriesExhausted, RetryPolicy
from .transformer import fingerprint, to_canonical, to_erp_xml
from .validation import ValidationFailed, parse_and_validate

log = logging.getLogger("mini_middleware.pipeline")


class Outcome(str, Enum):
    DELIVERED = "DELIVERED"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
    STALE_SKIPPED = "STALE_SKIPPED"
    DEAD_LETTERED = "DEAD_LETTERED"


@dataclass
class Result:
    outcome: Outcome
    correlation_id: str
    idempotency_key: str | None = None
    attempts: int = 0
    reason: str | None = None
    detail: str = ""
    dlq_id: int | None = None
    erp_response: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


class Pipeline:
    def __init__(
        self,
        config: Config,
        client: ErpClient | None = None,
        retry: RetryPolicy | None = None,
        store: IdempotencyStore | None = None,
        dead_letters: DeadLetterStore | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.config = config
        self.metrics = metrics or METRICS
        self.store = store or IdempotencyStore(config.db_path, config.lease_seconds)
        self.dlq = dead_letters or DeadLetterStore(config.db_path)
        self.client = client or ErpClient(
            config.erp_url,
            config.erp_token,
            config.erp_timeout,
            RateLimiter(config.rate_limit_per_sec),
        )
        self.retry = retry or RetryPolicy(config.max_attempts, config.backoff_base, config.backoff_cap)

    # ---- public API --------------------------------------------------------------

    def process(self, raw: bytes | str, source: str, correlation_id: str | None = None) -> Result:
        return self._run(raw, source, correlation_id or new_correlation_id(), replay_of=None)

    def replay(
        self,
        dlq_id: int,
        payload_override: bytes | str | None = None,
        correlation_id: str | None = None,
    ) -> Result:
        """Re-run a dead-lettered message, optionally with a corrected payload."""
        entry = self.dlq.get(dlq_id)
        if entry is None:
            raise KeyError(f"no dead letter with id {dlq_id}")
        if entry["status"] != "DEAD":
            raise ValueError(f"dead letter {dlq_id} is {entry['status']}, not DEAD")
        cid = correlation_id or new_correlation_id()
        with correlation(cid):
            log_event(log, "replay_started", dlq_id=dlq_id, override=payload_override is not None)
        payload = payload_override if payload_override is not None else entry["payload"]
        if payload_override is not None and entry["idempotency_key"]:
            # A corrected payload legitimately differs from what was recorded, so the old
            # dead-lettered record must not trigger a key-reuse conflict. The original
            # payload stays in the dead-letter row for audit.
            self.store.forget_dead(entry["idempotency_key"])
        result = self._run(payload, entry["source"], cid, replay_of=dlq_id)
        if result.outcome is not Outcome.DEAD_LETTERED:
            resolution = result.outcome.value + (" (corrected payload)" if payload_override else "")
            self.dlq.mark_replayed(dlq_id, resolution, cid)
            self.metrics.inc("replays_succeeded_total")
        return result

    # ---- internals ---------------------------------------------------------------

    def _run(self, raw: bytes | str, source: str, cid: str, replay_of: int | None) -> Result:
        with correlation(cid):
            self.metrics.inc("messages_received_total")
            text = self._to_text(raw)
            try:
                root = parse_and_validate(raw)
            except ValidationFailed as exc:
                self.metrics.inc("validation_failures_total")
                return self._dead_letter(
                    cid, None, exc.reason, "; ".join(exc.errors), 0, text, source, replay_of
                )
            msg = to_canonical(root)
            key = msg.idempotency_key
            status = self.store.begin(key, fingerprint(raw), cid)
            log_event(log, "idempotency_check", key=key, status=status.value, source=source)

            if status is BeginStatus.DUPLICATE:
                self.metrics.inc("duplicates_skipped_total")
                return Result(Outcome.DUPLICATE_SKIPPED, cid, key, detail="already delivered")
            if status is BeginStatus.IN_FLIGHT:
                self.metrics.inc("duplicates_skipped_total")
                return Result(Outcome.DUPLICATE_SKIPPED, cid, key, detail="in flight elsewhere")
            if status is BeginStatus.CONFLICT:
                return self._dead_letter(
                    cid, key, dlq_mod.KEY_REUSE_CONFLICT,
                    "same idempotency key seen earlier with different content",
                    0, text, source, replay_of,
                )

            if self.store.is_stale(msg.reference, msg.sequence):
                self.store.mark_stale(key, msg.reference, msg.sequence)
                self.metrics.inc("stale_skipped_total")
                log_event(
                    log, "stale_skipped", logging.WARNING, reference=msg.reference,
                    sequence=msg.sequence, newest_delivered=self.store.last_sequence(msg.reference),
                )
                return Result(Outcome.STALE_SKIPPED, cid, key, detail="newer sequence already delivered")

            body = to_erp_xml(msg)
            try:
                outcome, attempts = self.retry.run(
                    lambda n: self._attempt(n, body, key, cid, msg.reference),
                    on_retry=lambda n, delay, exc: self._on_retry(n, delay, exc),
                )
            except PermanentDeliveryError as exc:
                self.store.mark_dead(key, exc.attempts, str(exc))
                return self._dead_letter(
                    cid, key, dlq_mod.PERMANENT_REJECTION, str(exc), exc.attempts, text, source, replay_of
                )
            except RetriesExhausted as exc:
                self.store.mark_dead(key, exc.attempts, str(exc.last_error))
                return self._dead_letter(
                    cid, key, dlq_mod.RETRIES_EXHAUSTED, str(exc.last_error), exc.attempts, text,
                    source, replay_of,
                )

            self.store.mark_delivered(key, attempts, msg.reference, msg.sequence)
            self.metrics.inc("delivered_total")
            log_event(
                log, "delivered", key=key, reference=msg.reference, attempts=attempts,
                erp_response=outcome.value,
            )
            return Result(Outcome.DELIVERED, cid, key, attempts=attempts, erp_response=outcome.value)

    def _attempt(self, n: int, body: bytes, key: str, cid: str, reference: str):
        self.metrics.inc("delivery_attempts_total")
        try:
            return self.client.send(body, key, cid)
        except Exception as exc:
            log_event(
                log, "delivery_attempt_failed", logging.WARNING, attempt=n, reference=reference,
                error_class=type(exc).__name__, error=str(exc),
            )
            raise

    def _on_retry(self, attempt: int, delay: float, exc: Exception) -> None:
        self.metrics.inc("retries_total")
        log_event(log, "retry_scheduled", logging.WARNING, after_attempt=attempt, delay_s=round(delay, 3))

    def _dead_letter(
        self, cid: str, key: str | None, reason: str, detail: str, attempts: int,
        payload: str, source: str, replay_of: int | None,
    ) -> Result:
        if replay_of is not None:
            self.dlq.record_replay_failure(
                replay_of, correlation_id=cid, reason=reason, detail=detail, attempts=attempts
            )
            dlq_id = replay_of
        else:
            dlq_id = self.dlq.add(
                key=key, correlation_id=cid, source=source, reason=reason,
                detail=detail, attempts=attempts, payload=payload,
            )
        self.metrics.inc("dead_lettered_total")
        log_event(
            log, "dead_lettered", logging.ERROR, dlq_id=dlq_id, reason=reason, attempts=attempts,
            detail=detail[:300], replay=replay_of is not None,
        )
        return Result(
            Outcome.DEAD_LETTERED, cid, key, attempts=attempts, reason=reason, detail=detail,
            dlq_id=dlq_id,
        )

    @staticmethod
    def _to_text(raw: bytes | str) -> str:
        if isinstance(raw, str):
            return raw
        return raw.decode("utf-8", errors="replace")
