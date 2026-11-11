# Integration patterns catalogue

> Independent, unofficial reference. Not affiliated with or endorsed by WiseTech Global. Message shapes are illustrative, and no proprietary material is included.

Each pattern states the problem, the approach, where it appears in this repository, and the trade-offs. "Implemented" means there is working, tested code. "Documented" means the pattern is described here but deliberately not built.

| Pattern | Status |
|---|---|
| [Transport: file drop, HTTP, queue](#transport) | File drop and HTTP implemented; queue documented |
| [Idempotency](#idempotency) | Implemented |
| [Deduplication keys](#deduplication-keys) | Implemented |
| [Correlation ids](#correlation-ids) | Implemented |
| [Retry with exponential backoff and jitter](#retry-with-exponential-backoff-and-jitter) | Implemented |
| [Dead-letter queue](#dead-letter-queue) | Implemented |
| [Poison messages](#poison-messages) | Implemented |
| [Ordering](#ordering) | Partly implemented (per-shipment sequence guard) |
| [Schema validation](#schema-validation) | Implemented (subset validator) |
| [Reconciliation jobs](#reconciliation-jobs) | Implemented (on demand) |
| [Rate limiting](#rate-limiting) | Implemented (token bucket, off by default) |
| [Observability](#observability) | Logs and counters implemented; alerts documented |
| [Security](#security) | Partly implemented; the rest documented |
| [Circuit breaker, outbox, batching](#patterns-documented-but-not-built) | Documented |

## Transport

**Problem.** Senders and receivers rarely share a transport. The integration layer must accept what senders can produce and deliver what the ERP can consume.

| | File drop | HTTP request | Message queue |
|---|---|---|---|
| Sender effort | Lowest: write a file | Moderate: an HTTP client | Highest: a broker client |
| Delivery feedback to sender | None, unless a reply file or other channel is added | Immediate status code | Acknowledgement only; business outcomes need another channel |
| Durability before processing | The file system itself | Only if the receiver persists before replying | Built in, depending on broker configuration |
| Back-pressure | Files pile up (visible, but unbounded) | Sender sees timeouts and errors | Queue depth, with consumer scaling |
| Ordering | By file name, if senders cooperate | By sender behaviour | Per queue or partition, depending on the broker |
| Typical failure | Half-written files, permission drift, silent disk fill | Timeouts that provoke duplicate sends | Poison messages blocking a queue; unbounded redelivery |
| Operational weight | Very light | Light | Heaviest: broker to run, secure, monitor |

**In this repository.**

* `adapters/folder.py`: senders write to a temporary name and rename to `*.xml`; the watcher claims a file by atomic rename into `processing/`, so two workers cannot take the same file. A settle window guards against half-written files, and files stranded by a crash are returned to the inbox on start-up.
* `adapters/http_inbound.py`: status codes carry meaning. `200` means processed (including duplicates), `422` means the sender must fix the message, `409` means key reuse with different content, `202` means accepted into custody but delivery needs a replay.
* Queue: not built. [ADR 0001](adr/0001-direct-http-delivery-versus-queue.md) explains when to add one.

**Trade-off to remember.** Whatever the transport, the sender and the integration layer disagree about "done" for a moment. Idempotency (next section) is what makes that disagreement harmless.

## Idempotency

**Problem.** Networks lose acknowledgements. A sender that times out must retry, so the receiver will see the same message twice. Processing it twice may mean a duplicate shipment record, a double charge or a duplicated event.

**Approach.** Make processing of the same message safe to repeat.

1. Derive a stable key from the message (see below).
2. Before doing work, atomically claim the key in a store. A first claim proceeds, a repeat of a completed message is skipped, and a claim held by a live worker is left alone.
3. Send the same key to the downstream system (`Idempotency-Key` header), so that it can deduplicate too. Treat its "already processed" answer (`409` in the mock) as success.

**In this repository.** `idempotency.py` (`begin`, `mark_delivered`, `mark_dead`), driven from `pipeline.py`. A claim carries a lease (`lease_seconds`, default 60). If a worker dies, the lease expires and the next delivery resumes the message instead of leaving it stuck. Tests: `test_stores.py`, and the lost-acknowledgement test in `test_integration.py`.

**Trade-offs.**

* The store becomes a critical dependency. If it is lost, protection against duplicates is lost with it.
* "Exactly once" is not achievable across two systems. The realistic goal is at-least-once delivery combined with idempotent handling, which behaves like exactly once.
* Retention: keys cannot be kept forever. Choose a window longer than the longest plausible redelivery.

## Deduplication keys

**Problem.** Which part of a message identifies it?

| Option | Works when | Fails when |
|---|---|---|
| Sender-assigned message id plus sender id (used here) | The sender is disciplined about ids | The sender regenerates ids on resend |
| Business key, such as shipment reference plus event code plus timestamp | Ids are missing or unreliable | Two legitimate events share the same business key |
| Content hash only | No ids exist at all | A harmless change, such as a reformatted timestamp, defeats it |

**In this repository.** The key is `sender:messageId`. A second value, the *fingerprint* (SHA-256 of the canonicalised XML), is stored beside it. Same key and same fingerprint is a duplicate. Same key and a different fingerprint is a **key-reuse conflict**: someone is reusing an id for different content, so the message goes to the dead-letter store rather than being silently dropped or silently overwritten. Canonicalisation means whitespace-only differences do not count.

## Correlation ids

**Problem.** When something goes wrong at 02:00, someone must be able to follow one message across adapters, retries, the database and the ERP.

**Approach.** Take the sender's `X-Correlation-Id` if it is well formed, otherwise generate one. Bind it to the processing context, attach it to every log line, send it to the ERP in a header, and store it with the idempotency record and dead letter. Reject odd values (the HTTP adapter accepts only `[A-Za-z0-9._-]{1,64}`) so that a hostile header cannot inject log content.

**In this repository.** `logging_setup.py` holds the id in a `contextvars` variable, so it follows the work without being passed through every function. A replay gets a new correlation id, and a `replay_started` log line records the dead-letter id, which links the original failure to the later attempt. The dead-letter row keeps only the latest attempt's correlation id, so the original id survives in the earlier log lines.

## Retry with exponential backoff and jitter

**Problem.** Retrying immediately and repeatedly turns a brief ERP slowdown into an outage, and many clients retrying in step produce a "thundering herd".

**Approach.**

* Retry only **transient** failures: timeouts, connection errors, `5xx`, `408`, `429`.
* Wait longer after each failure: the ceiling is `min(cap, base * 2^(attempt - 1))`.
* Use **full jitter**: wait a random time between zero and the ceiling, so that retries from many messages spread out.
* Respect `Retry-After` as a lower bound.
* Stop after a bounded number of attempts and dead-letter the message.

**In this repository.** `retry.py` (`backoff_delay`, `RetryPolicy`). The random generator and the sleep function are injectable, so tests are fast and deterministic. Tests: `test_retry_and_ratelimit.py`.

**Trade-offs.** Longer waits protect the ERP but delay delivery. Bounded retries bound the work done during an outage, at the price of needing a replay step afterwards. Retrying inside a synchronous HTTP request also ties up a sender connection (see ADR 0001).

## Dead-letter queue

**Problem.** Some messages cannot be delivered now. Dropping them loses data. Retrying them forever blocks everything behind them and hides the problem.

**Approach.** Move the message aside with enough context to decide what to do: the payload, a reason code, the error detail, the number of attempts, the correlation id and the source. Provide tools to list, inspect, replay and discard. Record every operator decision.

Reason codes in this repository: `MALFORMED_XML`, `VALIDATION_FAILED`, `KEY_REUSE_CONFLICT`, `PERMANENT_REJECTION`, `RETRIES_EXHAUSTED`.

**In this repository.** `dlq.py`, the `dlq` CLI commands, and `Pipeline.replay`. A replay re-enters the full pipeline, so validation, deduplication and ordering checks apply again. A replay may carry a corrected payload (`--payload-file`); the original is kept for audit. A discard requires a reason.

**Trade-offs.** A dead-letter queue nobody watches is a data-loss mechanism with extra steps. Alert on its depth and age (see [Observability](#observability)). Replay after a long delay can deliver stale data, which is one reason the ordering guard exists.

## Poison messages

**Problem.** A message that can never succeed, whatever the retry count, because it is malformed, violates the schema or breaks a business rule.

**Approach.** Detect it early and classify it as permanent. Never retry it. Route it straight to the dead-letter store so it cannot consume retry budget or block others.

**In this repository.**

* Malformed or schema-invalid input never reaches the ERP (`validation.py`).
* A `4xx` from the ERP other than `408`, `409` and `429` is treated as permanent (`delivery.py`).
* DOCTYPE and ENTITY declarations are refused, which removes the XML entity-expansion class of attack without a third-party parser.

**Trade-off.** Classification is a judgement. Treating a `4xx` as permanent is usually right, but a misconfigured credential also produces `401`. A burst of `PERMANENT_REJECTION` with `401` in the detail is a configuration problem, not a data problem (see the [runbook](failure-modes-runbook.md)).

## Ordering

**Problem.** Updates to the same shipment may arrive out of order: a "delivered" status ahead of "departed", or an old update replayed from the dead-letter store after a newer one has gone through.

**Options.**

1. Strict FIFO per shipment. Simple to reason about, but one stuck message blocks the shipment behind it, and it needs partitioning to scale.
2. Sequence guard. Each message carries a per-shipment sequence number; an update older than the newest delivered one is not applied.
3. Last-writer-wins by timestamp. Easy, but clocks disagree.
4. Ignore ordering. Acceptable only when updates are commutative.

**In this repository.** Option 2. `shipment_state.last_sequence` records the newest delivered sequence for each shipment reference. A message at or below it is marked `STALE` and skipped, with a warning log. This protects against late arrivals and late replays. It does **not** guarantee that a missing sequence number is waited for, so a gap is not detected.

**Trade-off.** If the ERP must receive every intermediate event, a sequence guard that skips old ones is wrong for that stream. Choose per message type.

## Schema validation

**Problem.** Garbage in, garbage in the ERP. Validation errors are cheapest to fix at the boundary, with the sender still in view.

**Approach.** Validate against an XSD before any side effect. Report the path and reason for every error so the sender can fix it. Reject unknown versions explicitly.

**In this repository.** `schemas/shipment_message.xsd` is an illustrative schema, written for this repository. The Python standard library cannot validate against XSD, so `xsd.py` implements the subset the schema needs (sequences, occurrence limits, required attributes, enumerations, patterns, numeric and length facets, simple content). The test suite cross-checks it against lxml on a set of valid and invalid documents when lxml is installed. Versioning is covered in [ADR 0004](adr/0004-schema-validation-and-versioning.md).

**Trade-offs.** A subset validator is a liability if the schemas grow. With real, complex schemas, use a complete XSD engine.

## Reconciliation jobs

**Problem.** Controls fail quietly. A record can be lost on the ERP side after a successful acknowledgement, a worker can die at an awkward moment, or someone can edit data by hand. Nothing in the live path will notice.

**Approach.** Periodically compare independent views of the truth and report the differences.

**In this repository.** `reconcile.py` and `python -m mini_middleware reconcile`. It compares keys that the middleware believes are delivered against the records the ERP reports, and lists:

* `MISSING_IN_ERP`: we say delivered, the ERP has no record
* `UNEXPECTED_IN_ERP`: the ERP has it, we have no delivery record
* `REFERENCE_MISMATCH`: the same key maps to different shipment references
* open dead letters, and messages stuck `IN_PROGRESS` past their lease

The exit code is `0` when clean and `2` when something needs attention, so a scheduler can alert on it.

**Trade-offs.** Reconciliation finds problems, it does not fix them. Decide in advance who owns each kind of discrepancy. For large volumes, compare by time window or by checksum rather than by full listing.

## Rate limiting

**Problem.** A backlog released after an outage, or a bulk load, can overwhelm the ERP, which then fails, which creates more retries.

**Approach.** A token bucket in front of the delivery client spaces out calls (`MM_RATE_LIMIT`, calls per second; `0` disables it). Combined with `Retry-After` handling, this makes the integration a considerate client.

**In this repository.** `ratelimit.py`, applied in `delivery.py`. The clock and sleep are injectable for testing.

**Trade-off.** A limit set too low creates its own backlog. Set it from the ERP owner's guidance, not from guesswork.

## Observability

**Problem.** "It works" is not an answer to "what happened to this message?".

**Logs.** One JSON object per line, with a timestamp, level, event name, correlation id and event-specific fields. Event names are stable (`idempotency_check`, `delivery_attempt_failed`, `retry_scheduled`, `delivered`, `dead_lettered`, `stale_skipped`, `replay_started`), so they can be searched and counted. Fields whose names suggest secrets (`token`, `authorization`, `password`, `secret`, `api_key`) are redacted.

**Metrics.** In-process counters (`messages_received_total`, `delivered_total`, `delivery_attempts_total`, `retries_total`, `duplicates_skipped_total`, `stale_skipped_total`, `validation_failures_total`, `dead_lettered_total`, `replays_succeeded_total`), exposed at `/metrics` in Prometheus text format.

**Alerts to consider.** These are suggestions, not tested configuration, and thresholds depend on volume.

| Signal | Why it matters |
|---|---|
| Dead-letter depth above zero for longer than an agreed time | Messages are waiting on a person |
| Oldest dead letter older than a threshold | Replay risk grows with age |
| Retry rate rising relative to deliveries | ERP is degrading before it fails outright |
| Validation failures from one sender spiking | Sender changed something, or a schema drifted |
| Delivery attempts with no deliveries for a period during working hours | Pipeline is stuck or the ERP is down |
| Messages `IN_PROGRESS` beyond their lease | A worker died |
| Reconciliation exit code non-zero | Independent evidence of a discrepancy |

## Security

| Concern | Approach here | Notes |
|---|---|---|
| Secrets | Read from environment variables (`MM_ERP_TOKEN`, `MM_HTTP_TOKEN`); never written to logs (redaction) or to the database | In a real deployment, use a secrets manager and rotate |
| Least privilege | The ERP credential should be able to post messages and read back records for reconciliation, and nothing else. The database file should be readable only by the service account | The mock ERP only checks a bearer token; real scopes are the real ERP's business |
| Inbound authentication | Optional bearer token on the HTTP endpoint, compared in constant time | Prefer mutual TLS or a gateway for real senders |
| Input hardening | Size limit, `Content-Length` required, DOCTYPE and ENTITY refused, correlation id sanitised | Add a request rate limit at the edge |
| Redirects | The delivery client does not follow redirects, so a POST cannot be silently turned into a GET or sent elsewhere | |
| Audit | Every state change is recorded: idempotency status, attempts, dead-letter reason, replay count, discard reason | Ship logs to write-once storage if audit requirements demand it |
| Data at rest | Payloads are stored in the dead-letter table so they can be replayed | Treat the database as sensitive. Apply retention limits and encryption suitable to the data |
| Transport | The reference uses plain HTTP on localhost | Use TLS everywhere outside a development machine |

## Patterns documented but not built

* **Circuit breaker.** After repeated failures, stop calling the ERP for a cool-down period instead of burning retries. Worth adding when several workers share one ERP.
* **Transactional outbox.** When the integration also writes to a local database as part of the same business action, write the message to an outbox table in the same transaction, and publish from there, to avoid the "saved but never sent" gap.
* **Batching.** Group several messages in one request when the ERP's interface prefers it. This complicates partial failure handling: decide how a batch with one bad message is treated.
* **Back-pressure on inbound.** Reject or slow senders when the dead-letter queue or backlog exceeds a threshold.
