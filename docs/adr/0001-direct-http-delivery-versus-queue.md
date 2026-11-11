# 0001: Direct HTTP delivery with durable state, versus a queue

* Status: accepted for the reference implementation (a production design would likely supersede it)
* Date: 2026-11-04

## Context and problem statement

Messages arrive from senders (files, HTTP) and must reach an ERP that can be slow, unavailable or inconsistent. Should the integration layer deliver directly during intake, or place each message on a durable queue and deliver from workers?

## Decision drivers

* The repository must run with the Python standard library only, with no broker to install
* The code should be readable end to end by a reviewer
* Failure handling must still be real: durable records, retries, dead-lettering, replay
* The limits of the simpler option must be stated honestly

## Considered options

1. Direct, synchronous delivery during intake, with durable state in a database
2. Persist-then-acknowledge: write the message to a durable inbox, return `202`, deliver from workers
3. External message broker with consumers and a broker-native dead-letter queue

## Decision outcome

Chosen option: **1**, for the reference implementation only.

### Consequences

* Good: very little machinery, so the patterns (idempotency, retry, dead-lettering, replay) stay visible.
* Good: the state store still makes every outcome durable, so a crash does not lose the record of what happened.
* Bad: an HTTP sender waits while retries run, and may time out and resend. Idempotency makes the resend safe, but it is wasteful.
* Bad: a crash between receiving and recording can lose an HTTP message that the sender believes was received. The sender's own retry covers it only if the sender retries on connection failure.
* Bad: throughput is limited by the intake threads.
* Neutral: moving to option 2 changes the adapters, not the pipeline. The pipeline already works on a stored message plus a correlation id.

## Pros and cons of the options

### Direct synchronous delivery

* Good: simplest, no extra component
* Bad: couples sender latency to ERP latency; weak durability at intake

### Persist-then-acknowledge

* Good: sender gets a fast, honest answer ("we hold it"); retries happen off the request path; backlog is visible
* Good: workers scale independently, and the lease design in ADR 0002 already allows several
* Bad: one more moving part, and a need to define how senders learn the final outcome

### External broker

* Good: mature queuing, redelivery, dead-lettering and back-pressure
* Bad: a system to run, secure and monitor; poison messages and redelivery loops need care; heavier for small volumes

## More information

When volumes grow, when senders cannot tolerate long responses, or when several workers are needed, adopt option 2 first, since it keeps the pipeline and adds a durable inbox. Move to a broker when queue features (partitioning, fan-out, retention) are genuinely needed. See the [build versus buy guide](../build-vs-buy.md).
