# 0006: Ordering by per-shipment sequence guard

* Status: accepted
* Date: 2026-11-04

## Context and problem statement

Status updates for one shipment can arrive out of order, and a replayed dead letter can arrive long after newer updates. Applying an old update after a new one would make the ERP wrong. How should ordering be handled?

## Decision drivers

* Correctness of the latest state
* Do not let one stuck message block unrelated shipments
* Make the rule explicit and testable

## Considered options

1. Strict FIFO per shipment (hold later messages until earlier ones succeed)
2. A per-shipment sequence number from the sender; skip any update at or below the newest delivered sequence
3. Last-writer-wins on the sender's timestamp
4. No ordering control

## Decision outcome

Chosen option: **2**.

The `Sequence` header element must be a positive integer, increasing per shipment reference. `shipment_state.last_sequence` records the highest delivered value. Anything at or below it is marked `STALE`, logged as a warning and counted, but not delivered.

### Consequences

* Good: no head-of-line blocking; late arrivals and late replays cannot overwrite newer state.
* Good: the rule is small, visible and tested (`test_stale_update_is_not_delivered`).
* Bad: gaps are not detected. If sequence 3 is delivered while 2 is still in the dead-letter store, 2 will be skipped on replay.
* Bad: it suits "latest state wins" streams. If every intermediate event must reach the ERP, the rule is wrong for that stream, and option 1 or a different design is needed.
* Bad: it relies on senders numbering correctly.

## More information

Public third-party descriptions of ERP messaging note that event order matters to the receiver. Whether a given real interface needs strict ordering is a question for its documentation and owners, not something this repository decides.
