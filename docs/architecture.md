# Architecture overview and reference architecture

> Independent, unofficial reference. Not affiliated with or endorsed by WiseTech Global. CargoWise is a trademark of its owner. No real schemas, credentials, customer data or proprietary documentation are included. All message shapes here are illustrative and simplified.

## 1. Purpose and scope

This document describes a small integration layer ("middleware") that sits between systems that send shipment status messages and an ERP that receives them. The implementation in `src/mini_middleware/` is deliberately small enough to read in an afternoon, yet it exercises the problems that decide whether an integration is dependable in practice: duplicates, partial failures, out-of-order delivery, malformed input, downstream outages and the need to explain afterwards what happened to any given message.

What it is:

* a worked example of integration patterns, written with the Python standard library only
* a vehicle for discussing trade-offs (see the ADRs and the build-versus-buy guide)

What it is not:

* a CargoWise connector. It does not use, reproduce or approximate any real CargoWise schema, endpoint or authentication scheme.
* a production system. See the limitations in the [README](../README.md#limitations).

## 2. Public context

ERP integration in freight forwarding commonly involves exchanging XML messages with the ERP over a messaging interface (for CargoWise One, WiseTech's public material and third-party partner documentation describe this as the eAdaptor). Public third-party descriptions mention XML payloads that may be wrapped, compressed and batched, sent in the order they were generated, with order mattering to the receiver. Treat that as orientation only. Anyone building a real integration should work from the official documentation that their licence entitles them to, which this repository neither contains nor replaces.

The patterns below do not depend on those details. They apply to any ERP that accepts messages asynchronously and may fail, time out or receive the same message twice.

## 3. System context

```mermaid
flowchart LR
    subgraph Senders["Message senders (illustrative)"]
        P1["Partner system A\n(file drop)"]
        P2["Partner system B\n(HTTP POST)"]
    end

    subgraph MW["Integration layer (this repository)"]
        M["mini_middleware\nvalidate, deduplicate,\ntransform, deliver, record"]
    end

    ERP["ERP platform\n(accepts XML messages over HTTP;\nstand-in: mock ERP)"]
    OPS["Operations team\n(dead-letter CLI, reconciliation report)"]
    OBS["Observability\n(JSON logs, counters)"]

    P1 -->|"XML files"| M
    P2 -->|"XML over HTTP"| M
    M -->|"transformed XML + Idempotency-Key"| ERP
    ERP -->|"201 / 409 / 4xx / 5xx"| M
    M -->|"structured logs, metrics"| OBS
    OPS -->|"list, replay, discard, reconcile"| M
    M -.->|"read-back for reconciliation"| ERP
```

Rendered copies of every diagram are kept in [`docs/diagrams/`](diagrams/) as PNG files for viewers that do not render Mermaid.

![System context](diagrams/context.png)

## 4. Containers and components

```mermaid
flowchart TB
    F["Folder watcher"] --> V
    H["HTTP endpoint"] --> V

    subgraph PIPE["Pipeline"]
        V["1. Parse and XSD validate"] --> I["2. Idempotency check"]
        I --> O["3. Ordering guard"]
        O --> T["4. Transform"]
        T --> D["5. Deliver with retry,\nbackoff and jitter"]
    end

    D --> ERP["Mock ERP"]

    subgraph DB["SQLite"]
        S1[("messages,\nshipment_state")]
        S2[("dead_letters")]
    end

    I <--> S1
    D -->|"delivered or 409"| S1
    V -->|"poison"| S2
    D -->|"permanent or exhausted"| S2
    CLI["CLI: dlq list, replay,\ndiscard; reconcile"] --> S2
    CLI -->|"reconcile"| S1
    CLI -->|"reconcile"| ERP
```

![Container view](diagrams/container.png)

| Component | Module | Responsibility |
|---|---|---|
| Folder watcher | `adapters/folder.py` | Poll `inbox/`, claim files by atomic rename, move to `processed/` or `failed/` |
| HTTP endpoint | `adapters/http_inbound.py` | `POST /messages`, bearer-token check, size limit, correlation id intake, `/healthz`, `/metrics` |
| Validation | `validation.py`, `xsd.py`, `schemas/*.xsd` | Reject malformed XML and anything that fails the illustrative XSD |
| Transformer | `transformer.py` | Parse to a canonical model, normalise units, map to the ERP-side shape |
| Idempotency store | `idempotency.py`, `db.py` | Claim a key with a lease, detect duplicates, key reuse and stale updates |
| Delivery client | `delivery.py`, `ratelimit.py` | HTTP POST with timeout, no redirects, transient or permanent classification |
| Retry policy | `retry.py` | Exponential backoff with full jitter, honours `Retry-After` |
| Dead-letter store | `dlq.py` | Hold failed messages with a reason code, support replay and discard |
| Reconciliation | `reconcile.py` | Compare local delivery records with what the ERP reports holding |
| Observability | `logging_setup.py`, `metrics.py` | JSON logs with correlation ids, redaction, counters |
| Mock ERP | `mock_erp.py` | Deterministic misbehaviour for tests and the demo |
| CLI | `cli.py` | `ingest`, `watch`, `serve`, `dlq ...`, `reconcile`, `demo` |

## 5. Message lifecycle

Every message ends in exactly one terminal state, and the state is recorded.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> InProgress: new key, lease taken
    [*] --> DeadLettered: invalid, or key reused with different content
    [*] --> Skipped: duplicate
    InProgress --> Delivered: 201 or 409
    InProgress --> Stale: older sequence
    InProgress --> DeadLettered: permanent or exhausted
    DeadLettered --> InProgress: replay
    DeadLettered --> Discarded: discard with reason
    Delivered --> [*]
    Stale --> [*]
    Skipped --> [*]
    Discarded --> [*]
```

![Message lifecycle](diagrams/lifecycle.png)

Transient failures keep a message in `InProgress` while it is retried. If a worker dies mid-flight, its lease expires and the next delivery of the same message resumes it. Neither of those loops is drawn, to keep the picture readable.

## 6. Sequences

### 6.1 Happy path

```mermaid
sequenceDiagram
    autonumber
    participant S as Sender
    participant A as Adapter
    participant P as Pipeline
    participant DB as SQLite store
    participant E as ERP (mock)

    S->>A: XML message (optional correlation id)
    A->>P: process(raw, source, correlation id)
    P->>P: parse and validate against XSD
    P->>DB: begin(key, fingerprint)
    DB-->>P: NEW (lease taken)
    P->>DB: is newer sequence already delivered?
    DB-->>P: no
    P->>P: transform to ERP XML
    P->>E: POST with Idempotency-Key and correlation id
    E-->>P: 201 Created
    P->>DB: mark delivered, advance shipment sequence
    P-->>A: DELIVERED
    A-->>S: 200 (HTTP) or file moved to processed/
```

![Happy path sequence](diagrams/sequence-happy.png)

### 6.2 Failure path: lost acknowledgement, outage, dead letter and replay

```mermaid
sequenceDiagram
    autonumber
    participant P as Pipeline
    participant E as ERP (mock)
    participant DB as SQLite store
    participant Q as Dead-letter store
    participant Op as Operator (CLI)

    P->>E: POST (attempt 1)
    E--xP: timeout (ERP stored it, reply lost)
    Note over P: transient error, wait random 0..base
    P->>E: POST (attempt 2, same Idempotency-Key)
    E-->>P: 409 already processed
    Note over P: duplicate treated as success
    P->>DB: mark delivered

    Note over P,E: A different message during an outage
    P->>E: POST (attempts 1 to 4, growing jittered waits)
    E-->>P: 503 each time
    P->>DB: mark dead lettered
    P->>Q: add(reason RETRIES_EXHAUSTED, payload, attempts)
    Op->>Q: dlq list
    Note over E: ERP recovers
    Op->>P: dlq replay id
    P->>DB: begin (RESUMED)
    P->>E: POST
    E-->>P: 201 Created
    P->>DB: mark delivered
    P->>Q: mark replayed
```

![Failure path sequence](diagrams/sequence-failure.png)

The first half of this diagram is the subtle case. The ERP stored the message but the reply was lost, so the sender sees a timeout and retries. Because the retry carries the same `Idempotency-Key`, the ERP answers `409`, and the middleware treats that as success. Without a stable key, the same retry would create a second record.

## 7. Data model

Three SQLite tables, created by `db.py`:

| Table | Purpose | Key columns |
|---|---|---|
| `messages` | One row per idempotency key | `idempotency_key` (primary key), `fingerprint`, `status` (`IN_PROGRESS`, `DELIVERED`, `DEAD_LETTERED`, `STALE`), `attempts`, `lease_expires`, `correlation_id`, `last_error` |
| `shipment_state` | Highest delivered sequence per shipment reference | `reference`, `last_sequence` |
| `dead_letters` | Messages needing a human decision | `id`, `reason`, `detail`, `attempts`, `payload`, `status` (`DEAD`, `REPLAYED`, `DISCARDED`), `replay_count`, `resolution` |

## 8. Design principles

1. **Assume every message arrives more than once.** Safety comes from idempotency, not from hoping the network behaves.
2. **Separate "try again" from "give up".** Transient failures are retried. Permanent failures and exhausted retries go to the dead-letter store with a reason code. Nothing is silently dropped.
3. **Validate at the boundary.** Bad input is rejected before it can touch the ERP, and kept so someone can fix and replay it.
4. **Every decision leaves a record.** A correlation id follows a message through logs, the idempotency table and the dead-letter store.
5. **Trust, but verify.** Reconciliation is a detective control that finds what the other controls missed.
6. **Keep humans in the loop for judgement calls.** Replay and discard are explicit operator actions with an audit trail (`resolution`, `replay_count`).

## 9. From reference to real deployment

The reference implementation makes simplifications on purpose. A real deployment would typically change the following, and the ADRs explain the reasoning:

| Reference implementation | Typical real-world choice | ADR |
|---|---|---|
| Synchronous processing inside the HTTP request | Persist first, acknowledge with `202`, process on workers | [0001](adr/0001-direct-http-delivery-versus-queue.md) |
| SQLite on local disk | A managed relational database, or a broker with native dead-lettering | [0005](adr/0005-sqlite-as-state-store.md) |
| Environment-variable secrets | A secrets manager with rotation | [patterns catalogue](patterns-catalogue.md#security) |
| In-process counters | A metrics backend with alert rules | [patterns catalogue](patterns-catalogue.md#observability) |
| Subset XSD validator | A full XSD engine, if the real schemas need one | [0004](adr/0004-schema-validation-and-versioning.md) |
| Single process | Several workers, which the lease design already allows for | [0002](adr/0002-idempotency-strategy.md) |
