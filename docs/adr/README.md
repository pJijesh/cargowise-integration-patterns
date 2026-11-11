# Architecture decision records

Format: [MADR](https://adr.github.io/madr/) (Markdown Any Decision Records). Each record states the context, the options considered, the decision and its consequences. They describe decisions for this reference implementation. Several record a simplification made on purpose, and say what a production design would do instead.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-direct-http-delivery-versus-queue.md) | Direct HTTP delivery with durable state, versus a queue | Accepted for the reference implementation |
| [0002](0002-idempotency-strategy.md) | Idempotency: business key, fingerprint, lease and downstream key | Accepted |
| [0003](0003-error-classification-and-retry.md) | Error classification, retry with full jitter, dead-lettering | Accepted |
| [0004](0004-schema-validation-and-versioning.md) | Schema validation at the boundary, and versioning rules | Accepted |
| [0005](0005-sqlite-as-state-store.md) | SQLite as the state store | Accepted for the reference implementation |
| [0006](0006-ordering-strategy.md) | Ordering: per-shipment sequence guard | Accepted |
| [0007](0007-correlation-ids-and-structured-logging.md) | Correlation ids and structured JSON logging | Accepted |
| [0008](0008-reconciliation-as-a-detective-control.md) | Reconciliation as a detective control | Accepted |

To add one, copy the structure of an existing record, take the next number, and add a row above.
