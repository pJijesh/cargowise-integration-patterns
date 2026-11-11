# Failure modes and runbook

> Independent, unofficial reference. This runbook is written for the reference implementation in this repository and its mock ERP. It is an example of the format and the thinking, not an operating procedure for any real system.

All commands assume `PYTHONPATH=src` (or an editable install) and the same `MM_*` environment variables the service runs with. The examples use `python -m mini_middleware`.

## Quick reference

| Command | Use |
|---|---|
| `dlq list [--status DEAD\|REPLAYED\|DISCARDED\|ALL]` | What is waiting for a decision |
| `dlq show ID` | Full record, including payload and error detail |
| `dlq replay ID [--payload-file FIXED.xml]` | Re-run one message, optionally corrected |
| `dlq replay-all [--reason CODE]` | Re-run every open dead letter, optionally one reason only |
| `dlq discard ID --reason "text"` | Close a dead letter with a recorded reason |
| `reconcile [--json]` | Compare delivery records with the ERP. Exit `0` clean, `2` discrepancies |
| `watch --dir DIR --once` | Process the inbox once and exit |

Reason codes: `MALFORMED_XML`, `VALIDATION_FAILED`, `KEY_REUSE_CONFLICT`, `PERMANENT_REJECTION`, `RETRIES_EXHAUSTED`.

## Failure modes

| # | Failure mode | What you see | Likely cause | Built-in protection | First response |
|---|---|---|---|---|---|
| 1 | ERP down or overloaded | `delivery_attempt_failed` and `retry_scheduled` logs; then `dead_lettered` with `RETRIES_EXHAUSTED` | Outage, deployment, capacity | Bounded retries with jittered backoff, optional rate limit | [R1](#r1-erp-outage) |
| 2 | Reply lost (timeout after the ERP stored the message) | A timeout, then `delivered` with `erp_response=ALREADY_PROCESSED` | Network fault, slow ERP | Idempotency key plus `409` treated as success | None needed. Watch frequency |
| 3 | Duplicate from sender | `idempotency_check` with `status=DUPLICATE`; `duplicates_skipped_total` rises | Sender retry, replayed file | Idempotency store | None needed. If constant, ask the sender why |
| 4 | Same id, different content | `dead_lettered` with `KEY_REUSE_CONFLICT` | Sender reuses ids, or a bug | Fingerprint comparison | [R4](#r4-key-reuse-conflict) |
| 5 | Invalid or malformed message | `dead_lettered` with `VALIDATION_FAILED` or `MALFORMED_XML`; HTTP `422` | Sender bug, schema drift, truncated file | Boundary validation | [R3](#r3-validation-failures) |
| 6 | ERP rejects the message permanently | `dead_lettered` with `PERMANENT_REJECTION` | Business rule, bad reference data | `4xx` not retried | [R2](#r2-permanent-rejections) |
| 7 | ERP rejects the credential | `PERMANENT_REJECTION` with `401` in the detail, on many messages | Expired or wrong token | None. This is a configuration fault | [R5](#r5-credential-failure) |
| 8 | Out-of-order update | `stale_skipped` warning | Late arrival, late replay | Sequence guard | [R6](#r6-stale-updates) |
| 9 | Worker dies mid-message | Message `IN_PROGRESS` past its lease; reconciliation lists `STUCK_IN_PROGRESS` | Crash, kill, host restart | Lease expiry, resume on next delivery | [R7](#r7-stuck-in-progress) |
| 10 | ERP lost or never recorded a message | Reconciliation lists `MISSING_IN_ERP` | ERP-side fault or manual deletion | Reconciliation (detective) | [R8](#r8-reconciliation-discrepancy) |
| 11 | File never picked up | File remains in `inbox/` | Not ending `.xml`, still within settle window, watcher stopped | Orphan recovery on start | [R9](#r9-folder-adapter-problems) |
| 12 | Half-written file | `VALIDATION_FAILED` or `MALFORMED_XML` for a file that later looks fine | Sender wrote directly to the final name | Settle window | [R9](#r9-folder-adapter-problems) |
| 13 | Database unavailable or full | Exceptions in logs; folder files land in `failed/` | Disk full, permissions | Failure leaves the file in `failed/` rather than losing it | [R10](#r10-state-store-problems) |
| 14 | Replay produces old data | Replayed message delivers after a long delay | Dead letter left unattended | Sequence guard (partial) | Alert on dead-letter age |

## Procedures

### R1: ERP outage

1. Confirm it is the ERP, not the integration: `reconcile` still runs only if the ERP answers; otherwise check the ERP's own status channel.
2. Do not replay yet. Replaying into a failing ERP only creates more retries and more noise.
3. Watch `dlq list`. Messages accumulate with `RETRIES_EXHAUSTED`. That is the system working as designed.
4. When the ERP is healthy, replay in a controlled way: `dlq replay-all --reason RETRIES_EXHAUSTED`. If a rate limit is configured (`MM_RATE_LIMIT`) the replay respects it.
5. Run `reconcile`. Expect exit `0`. Anything else, go to [R8](#r8-reconciliation-discrepancy).
6. Record the incident: start, end, number of messages affected, whether any went stale.

### R2: Permanent rejections

1. `dlq show ID` and read `detail`. The ERP's reason is in it.
2. Decide: a data problem the sender must fix, reference data missing in the ERP, or a message that should never have been sent.
3. If the cause is fixable on our side (for example, reference data added), `dlq replay ID`.
4. If the sender must correct the message, ask them to resend with a **new** message id, or fix the payload yourself and `dlq replay ID --payload-file fixed.xml` (the original stays in the record).
5. If no action is needed, `dlq discard ID --reason "..."`. A discard without a reason is refused.

### R3: Validation failures

1. `dlq list`, then `dlq show ID`. The `detail` lists each error with its path, for example `/ShipmentMessage/Shipment/Mode: 'TELEPORT' is not one of [...]`.
2. One bad message: correct and replay with `--payload-file`, or ask the sender to resend.
3. Many failures from one sender, all with the same error: the sender or the schema has changed. Pause replays, contact the sender, and agree a fix before replaying anything.
4. If a schema change is legitimate, follow the versioning process in [ADR 0004](adr/0004-schema-validation-and-versioning.md) rather than loosening the validator.

### R4: Key reuse conflict

1. `dlq show ID` to see the new payload, and look at the stored record for the original key.
2. Typical causes: a sender that restarts its counter, or one that regenerates ids on resend after editing content.
3. Decide which content is correct. If the new one is, `dlq replay ID --payload-file` with the same content is not enough, because the key still belongs to the earlier message. Ask the sender for a new id.
4. Raise it with the sender as a defect. This must not become routine.

### R5: Credential failure

1. Symptoms: a run of `PERMANENT_REJECTION` entries with `401` (or `403`) in `detail`, across unrelated shipments.
2. Stop sending traffic if you can (pause the watcher or inbound endpoint). Every message in the burst is being dead-lettered for a reason unrelated to its content.
3. Fix the credential in the secrets store and restart the service.
4. `dlq replay-all --reason PERMANENT_REJECTION`, after checking with `dlq list` that every entry in that set is credential-related. Discard or handle others individually.

### R6: Stale updates

1. A `stale_skipped` warning means a newer sequence for the same shipment was already delivered. Usually this is correct and needs no action.
2. If a stale skip looks wrong (for example, the stream does not use sequence numbers the way the guard assumes), do not disable the guard. Fix the sender's sequencing, or change the guard for that message type and record the decision in an ADR.

### R7: Stuck in progress

1. `reconcile` lists `STUCK_IN_PROGRESS` rows whose lease has expired.
2. Nothing needs doing for the message itself: the next delivery of the same message resumes it. For a file, `watch` start-up returns stranded files from `processing/` to `inbox/`.
3. Investigate why the worker died: host restarts, memory limits, an unhandled exception in the logs.

### R8: Reconciliation discrepancy

| Finding | Meaning | Action |
|---|---|---|
| `MISSING_IN_ERP` | We recorded delivery but the ERP does not hold it | Ask the ERP owner whether it was removed. If it should exist, discuss re-delivery with a new idempotency key, because the old key is recorded as delivered |
| `UNEXPECTED_IN_ERP` | The ERP holds something we have no delivery record for | Check for another sender, a manual entry, or a worker that died between the ERP accepting and us recording |
| `REFERENCE_MISMATCH` | Same key, different shipment reference | Treat as a data integrity incident |
| Open dead letters | Work is waiting for a decision | Triage with `dlq list` |
| `STUCK_IN_PROGRESS` | See R7 | |

### R9: Folder adapter problems

* Senders must write to a temporary name and rename to `*.xml` when complete. Files that do not end in `.xml` are ignored by design.
* A file younger than the settle window (`settle_seconds`) is left for the next scan.
* Failures end in `failed/`; the payload is also in the dead-letter store for validation and delivery failures. A file in `failed/` with no dead letter means an unexpected error or an oversize file: read the `unexpected_error` or `file_rejected_too_large` log line.

### R10: State store problems

1. If the database cannot be written, processing cannot be made safe, so the pipeline raises rather than guess. Folder files move to `failed/` and an `unexpected_error` is logged.
2. Free space or fix permissions, then move files back to `inbox/`. Duplicates are harmless once the store works again, provided the store was not lost.
3. If the database file was lost, idempotency history is gone. Expect the ERP's own `409` answers to be the only remaining duplicate protection, and run `reconcile` once the ERP is reachable.

## After any incident

* Run `reconcile` and keep the output with the incident record.
* Check that the dead-letter queue is empty or explained.
* Ask whether a control failed or whether the control worked and a human decision was needed. Then decide whether an alert, a test or an ADR should change.
