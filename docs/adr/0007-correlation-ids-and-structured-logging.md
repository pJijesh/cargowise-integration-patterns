# 0007: Correlation ids and structured JSON logging

* Status: accepted
* Date: 2026-11-04

## Context and problem statement

Support staff need to follow one message through intake, retries, the database, the ERP and any later replay, and to search and count events reliably.

## Decision drivers

* One identifier visible everywhere
* Logs that machines can parse and humans can read
* No secrets in logs
* No hostile input in logs

## Considered options

1. A correlation id in a `contextvars` variable, attached to every JSON log line, sent downstream and stored with records
2. Pass a correlation id parameter through every function
3. Plain-text logs with the id in the message string

## Decision outcome

Chosen option: **1**.

* Intake takes `X-Correlation-Id` if it matches `[A-Za-z0-9._-]{1,64}`, otherwise generates one
* The pipeline binds it for the duration of processing; every log line carries it
* It is sent to the ERP as `X-Correlation-Id` and stored with the idempotency row and dead letter
* Log events have stable names; field names that suggest secrets are redacted
* Counters are kept in process and exposed at `/metrics`

### Consequences

* Good: a single search shows a message's whole story, including retries.
* Good: sanitising the inbound id prevents log injection.
* Bad: a replay uses a fresh id, and the dead-letter row keeps only the latest, so the link back to the first attempt is the dead-letter id in the `replay_started` log line.
* Bad: redaction by field name is a safety net, not a guarantee. The primary defence is not logging secrets at all.
* Bad: in-process counters reset on restart. A real deployment should use a metrics backend.

## More information

[Patterns catalogue: observability](../patterns-catalogue.md#observability). A real deployment would also add distributed tracing if several services are involved.
