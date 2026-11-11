# 0002: Idempotency by business key, fingerprint, lease and downstream key

* Status: accepted
* Date: 2026-11-04

## Context and problem statement

Retries, sender resends and replays mean the same message will be processed more than once. How do we ensure that repeated processing has the same effect as processing once, without blocking legitimate corrections?

## Decision drivers

* Safe under concurrent workers and under crashes
* Distinguish a true duplicate from a reused id with different content
* Work even when the downstream acknowledgement is lost
* Leave an explanation behind for operators

## Considered options

1. Sender message id plus sender id as the key, with a content fingerprint, a processing lease, and the same key sent downstream
2. Content hash only
3. Business key (shipment reference, event code, timestamp)
4. No deduplication; rely on the ERP to reject duplicates

## Decision outcome

Chosen option: **1**.

* Key: `sender:messageId`
* Fingerprint: SHA-256 of canonicalised XML (whitespace-insensitive)
* Claim: an atomic insert or update in one `BEGIN IMMEDIATE` transaction, with a lease (default 60 seconds)
* Downstream: the same key is sent as `Idempotency-Key`; a `409` from the ERP is treated as success

Outcomes of the claim: `NEW`, `RESUMED` (seen but never finished), `DUPLICATE` (already delivered), `IN_FLIGHT` (a live lease exists), `CONFLICT` (same key, different fingerprint).

### Consequences

* Good: safe if the ERP stored a message but the reply was lost (demo scenario 3 and an integration test).
* Good: a reused id with different content is surfaced as a dead letter rather than silently dropped.
* Good: a crashed worker does not strand a message, because its lease expires.
* Bad: depends on senders providing stable ids. Where they cannot, a business key must be agreed and documented.
* Bad: the store grows. A retention policy is needed (not implemented here); keep keys longer than any plausible redelivery.
* Bad: a corrected payload under an old key needs the old dead-lettered record cleared (`forget_dead`), which the replay command does only for records in `DEAD_LETTERED` state.

## Pros and cons of the options

### Content hash only

* Good: no sender cooperation needed
* Bad: a harmless change defeats it, and two real events with identical content are wrongly merged

### Business key

* Good: meaningful to operators
* Bad: legitimate repeated events collide; designing the key is hard

### Rely on the ERP

* Good: nothing to build
* Bad: you cannot assume the ERP deduplicates, and you learn about duplicates only after the damage

## More information

See the idempotency and deduplication sections of the [patterns catalogue](../patterns-catalogue.md). Tests: `tests/test_stores.py`, `tests/test_integration.py`.
