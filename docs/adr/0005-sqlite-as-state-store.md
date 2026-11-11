# 0005: SQLite as the state store

* Status: accepted for the reference implementation
* Date: 2026-11-04

## Context and problem statement

Idempotency records, per-shipment sequence state and dead letters need durable, transactional storage. What should hold them?

## Decision drivers

* No external services for the reference implementation
* Real transactions, so the claim-then-update logic is genuinely atomic
* Readable by anyone with a laptop
* An honest statement of where it stops being appropriate

## Considered options

1. SQLite in write-ahead logging mode, one short-lived connection per operation
2. A client-server relational database
3. A key-value store or broker with native dead-lettering
4. Files on disk

## Decision outcome

Chosen option: **1**.

Claims use `BEGIN IMMEDIATE` so the read and the write happen under one write lock. A connection is opened per operation, so threads never share one. The dead-letter table lives in the same database as the idempotency records, which keeps the "mark dead, then add dead letter" pair simple to reason about.

### Consequences

* Good: transactional, zero setup, tests run against a real database file.
* Good: one file to back up and inspect.
* Bad: a single writer, so throughput is limited, and the database is on local disk, so there is no high availability.
* Bad: several hosts cannot share it. Scaling out needs a client-server database. The `IdempotencyStore` and `DeadLetterStore` interfaces are small enough to re-implement for that.
* Bad: the `mark_dead` and `dlq.add` writes are separate transactions. A crash between them leaves a message marked dead without a dead-letter row. A production design would write both in one transaction, or reconcile the two.

## More information

If the design moves to a broker (see ADR 0001), keep a store for idempotency and audit, and let the broker own the dead-letter queue.
