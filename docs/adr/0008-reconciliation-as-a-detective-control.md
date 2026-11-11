# 0008: Reconciliation as a detective control

* Status: accepted
* Date: 2026-11-04

## Context and problem statement

Preventive controls (idempotency, validation, retries) can still fail: the ERP may lose or alter a record after acknowledging it, a worker may die between two steps, or someone may edit data by hand. How do we find what the live path missed?

## Decision drivers

* Independent evidence, not just our own records
* Usable from a scheduler, with a clear pass or fail
* Does not change data by itself

## Considered options

1. An on-demand command that compares local delivery records with the ERP's listing and reports differences
2. Continuous reconciliation inside the pipeline
3. No reconciliation; rely on user complaints

## Decision outcome

Chosen option: **1**.

`reconcile` reads delivered keys from the store and the ERP's records, and reports `MISSING_IN_ERP`, `UNEXPECTED_IN_ERP`, `REFERENCE_MISMATCH`, open dead letters and stuck in-progress messages. It exits `0` when clean and `2` otherwise. It is read-only.

### Consequences

* Good: surfaces problems that no other control can see, and gives operators a routine check after incidents.
* Good: the exit code works with any scheduler or alerting.
* Bad: it compares full listings. At real volume it should work by time window or by checksum.
* Bad: it detects, it does not repair. Ownership of each kind of finding must be agreed (see the runbook).
* Bad: it needs a read interface on the ERP. If none exists, reconciliation has to use exports or reports instead.

## More information

[Patterns catalogue: reconciliation](../patterns-catalogue.md#reconciliation-jobs) and runbook procedure R8.
