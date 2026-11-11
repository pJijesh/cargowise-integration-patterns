# 0003: Error classification, retry with full jitter, dead-lettering

* Status: accepted
* Date: 2026-11-04

## Context and problem statement

Delivery can fail in many ways. Retrying a failure that will never succeed wastes effort and delays the diagnosis. Giving up on one that would have succeeded loses work. How should failures be classified and handled?

## Decision drivers

* Never lose a message silently
* Do not hammer a struggling ERP
* Avoid synchronised retry storms
* Keep behaviour deterministic enough to test

## Considered options

1. Two classes, transient and permanent. Retry transient errors with exponential backoff and full jitter, a bounded number of times. Dead-letter everything that is permanent or exhausted.
2. Retry everything a fixed number of times at a fixed interval
3. Retry forever with backoff

## Decision outcome

Chosen option: **1**.

* Transient: timeouts, connection errors, `5xx`, `408`, `429` (with `Retry-After` as a lower bound)
* Success: `2xx`, and `409` (the ERP says it already has this key)
* Permanent: other `4xx`, and any `3xx` (redirects are not followed)
* Delay: random between 0 and `min(cap, base * 2^(attempt - 1))`
* Defaults: 4 attempts, base 0.5 seconds, cap 30 seconds
* Reason codes: `RETRIES_EXHAUSTED`, `PERMANENT_REJECTION`, `VALIDATION_FAILED`, `MALFORMED_XML`, `KEY_REUSE_CONFLICT`

### Consequences

* Good: poison messages fail fast and are visible; outages cost a bounded amount of work.
* Good: jitter spreads retries; seeded random generator and injectable sleep make tests fast.
* Bad: classification by status code is a heuristic. A misconfigured credential (`401`) is classed as permanent per message, which can flood the dead-letter store. The runbook covers it. A circuit breaker would be a better answer and is not built.
* Bad: bounded retries mean an outage longer than the retry window needs an operator replay.

## Pros and cons of the options

### Fixed retries at a fixed interval

* Bad: retries from many messages line up and land together

### Retry forever

* Good: no replay step
* Bad: hides permanent faults, grows unbounded queues, and ties up workers

## More information

[Patterns catalogue: retry](../patterns-catalogue.md#retry-with-exponential-backoff-and-jitter) and the [runbook](../failure-modes-runbook.md).
