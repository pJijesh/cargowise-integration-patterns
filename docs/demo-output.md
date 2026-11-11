# Demo output

Real output from `make demo`, captured on 2026-11-04 with Python 3.13.18.
Timestamps and correlation ids (where not set explicitly) differ on every run. The random suffix of the temporary directory name is replaced with XXXX; nothing else is edited.
Log lines are JSON, one per line. The scripted scenarios are described in the headings.

```text
mini_middleware demo (illustrative messages, mock ERP, temporary database)
working directory: /tmp/mm-demo-XXXX

=== Scenario 1: happy path via folder drop ===
{"ts": "2026-11-04T23:05:11.474+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "0a958151cc994431", "key": "PARTNER-A:MSG-1001", "status": "NEW", "source": "folder:ok-1001.xml"}
{"ts": "2026-11-04T23:05:11.478+00:00", "level": "INFO", "event": "delivered", "correlation_id": "0a958151cc994431", "key": "PARTNER-A:MSG-1001", "reference": "SIM-OK-1001", "attempts": 1, "erp_response": "ACCEPTED"}

=== Scenario 2: transient ERP failures are retried (HTTP endpoint; 500, 500, then success) ===
{"ts": "2026-11-04T23:05:11.499+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-retry", "key": "PARTNER-A:MSG-1002", "status": "NEW", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:11.503+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-retry", "attempt": 1, "reference": "SIM-FAIL500-2", "error_class": "TransientDeliveryError", "error": "ERP returned HTTP 500"}
{"ts": "2026-11-04T23:05:11.503+00:00", "level": "WARNING", "event": "retry_scheduled", "correlation_id": "demo-retry", "after_attempt": 1, "delay_s": 0.016}
{"ts": "2026-11-04T23:05:11.523+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-retry", "attempt": 2, "reference": "SIM-FAIL500-2", "error_class": "TransientDeliveryError", "error": "ERP returned HTTP 500"}
{"ts": "2026-11-04T23:05:11.523+00:00", "level": "WARNING", "event": "retry_scheduled", "correlation_id": "demo-retry", "after_attempt": 2, "delay_s": 0.015}
{"ts": "2026-11-04T23:05:11.553+00:00", "level": "INFO", "event": "delivered", "correlation_id": "demo-retry", "key": "PARTNER-A:MSG-1002", "reference": "SIM-FAIL500-2", "attempts": 3, "erp_response": "ACCEPTED"}
inbound HTTP 200: outcome=DELIVERED attempts=3

=== Scenario 3: lost acknowledgement: ERP stored it but the reply timed out; the retry gets 409 ===
{"ts": "2026-11-04T23:05:11.556+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "f69d534b05364f56", "key": "PARTNER-A:MSG-1003", "status": "NEW", "source": "folder:lostack-1003.xml"}
{"ts": "2026-11-04T23:05:11.958+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "f69d534b05364f56", "attempt": 1, "reference": "SIM-LOSTACK-1", "error_class": "TransientDeliveryError", "error": "TimeoutError: timed out"}
{"ts": "2026-11-04T23:05:11.958+00:00", "level": "WARNING", "event": "retry_scheduled", "correlation_id": "f69d534b05364f56", "after_attempt": 1, "delay_s": 0.033}
{"ts": "2026-11-04T23:05:11.994+00:00", "level": "INFO", "event": "delivered", "correlation_id": "f69d534b05364f56", "key": "PARTNER-A:MSG-1003", "reference": "SIM-LOSTACK-1", "attempts": 2, "erp_response": "ALREADY_PROCESSED"}

=== Scenario 4: poison messages go straight to the dead-letter queue, no retries ===
{"ts": "2026-11-04T23:05:11.997+00:00", "level": "ERROR", "event": "dead_lettered", "correlation_id": "demo-invalid", "dlq_id": 1, "reason": "VALIDATION_FAILED", "attempts": 0, "detail": "/ShipmentMessage/Shipment/Mode: 'TELEPORT' is not one of ['AIR', 'SEA', 'ROAD']", "replay": false}
inbound HTTP 422: outcome=DEAD_LETTERED reason=VALIDATION_FAILED
{"ts": "2026-11-04T23:05:12.000+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-reject", "key": "PARTNER-A:MSG-1005", "status": "NEW", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:12.002+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-reject", "attempt": 1, "reference": "SIM-REJECT-1005", "error_class": "PermanentDeliveryError", "error": "ERP returned HTTP 422: {\"error\": \"simulated business rule rejection\"}"}
{"ts": "2026-11-04T23:05:12.005+00:00", "level": "ERROR", "event": "dead_lettered", "correlation_id": "demo-reject", "dlq_id": 2, "reason": "PERMANENT_REJECTION", "attempts": 1, "detail": "ERP returned HTTP 422: {\"error\": \"simulated business rule rejection\"}", "replay": false}
inbound HTTP 202: outcome=DEAD_LETTERED reason=PERMANENT_REJECTION

=== Scenario 5: ERP outage: retries are exhausted and the message is dead-lettered ===
{"ts": "2026-11-04T23:05:12.008+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-outage", "key": "PARTNER-A:MSG-1006", "status": "NEW", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:12.010+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-outage", "attempt": 1, "reference": "SIM-OUTAGE-1006", "error_class": "TransientDeliveryError", "error": "ERP returned HTTP 503"}
{"ts": "2026-11-04T23:05:12.010+00:00", "level": "WARNING", "event": "retry_scheduled", "correlation_id": "demo-outage", "after_attempt": 1, "delay_s": 0.004}
{"ts": "2026-11-04T23:05:12.014+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-outage", "attempt": 2, "reference": "SIM-OUTAGE-1006", "error_class": "TransientDeliveryError", "error": "ERP returned HTTP 503"}
{"ts": "2026-11-04T23:05:12.014+00:00", "level": "WARNING", "event": "retry_scheduled", "correlation_id": "demo-outage", "after_attempt": 2, "delay_s": 0.054}
{"ts": "2026-11-04T23:05:12.069+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-outage", "attempt": 3, "reference": "SIM-OUTAGE-1006", "error_class": "TransientDeliveryError", "error": "ERP returned HTTP 503"}
{"ts": "2026-11-04T23:05:12.069+00:00", "level": "WARNING", "event": "retry_scheduled", "correlation_id": "demo-outage", "after_attempt": 3, "delay_s": 0.073}
{"ts": "2026-11-04T23:05:12.144+00:00", "level": "WARNING", "event": "delivery_attempt_failed", "correlation_id": "demo-outage", "attempt": 4, "reference": "SIM-OUTAGE-1006", "error_class": "TransientDeliveryError", "error": "ERP returned HTTP 503"}
{"ts": "2026-11-04T23:05:12.148+00:00", "level": "ERROR", "event": "dead_lettered", "correlation_id": "demo-outage", "dlq_id": 3, "reason": "RETRIES_EXHAUSTED", "attempts": 4, "detail": "ERP returned HTTP 503", "replay": false}
inbound HTTP 202: outcome=DEAD_LETTERED reason=RETRIES_EXHAUSTED attempts=4

--- dead-letter queue ---
  ID  STATUS    REASON               TRIES  KEY                          DETAIL
   1  DEAD      VALIDATION_FAILED        0  -                            /ShipmentMessage/Shipment/Mode: 'TELEPORT' is not one of ['A
   2  DEAD      PERMANENT_REJECTION      1  PARTNER-A:MSG-1005           ERP returned HTTP 422: {"error": "simulated business rule re
   3  DEAD      RETRIES_EXHAUSTED        4  PARTNER-A:MSG-1006           ERP returned HTTP 503

=== Scenario 6: ERP recovers; operator replays the outage victim; the invalid one is corrected and replayed ===
{"ts": "2026-11-04T23:05:12.151+00:00", "level": "INFO", "event": "replay_started", "correlation_id": "demo-replay-1", "dlq_id": 3, "override": false}
{"ts": "2026-11-04T23:05:12.153+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-replay-1", "key": "PARTNER-A:MSG-1006", "status": "RESUMED", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:12.156+00:00", "level": "INFO", "event": "delivered", "correlation_id": "demo-replay-1", "key": "PARTNER-A:MSG-1006", "reference": "SIM-OUTAGE-1006", "attempts": 1, "erp_response": "ACCEPTED"}
replay 3: DELIVERED attempts=1
{"ts": "2026-11-04T23:05:12.158+00:00", "level": "INFO", "event": "replay_started", "correlation_id": "demo-replay-2", "dlq_id": 1, "override": true}
{"ts": "2026-11-04T23:05:12.161+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-replay-2", "key": "PARTNER-A:MSG-1004", "status": "NEW", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:12.165+00:00", "level": "INFO", "event": "delivered", "correlation_id": "demo-replay-2", "key": "PARTNER-A:MSG-1004", "reference": "SIM-OK-1004", "attempts": 1, "erp_response": "ACCEPTED"}
replay 1 with corrected payload: DELIVERED
discarded 2 after review (a human decision, recorded with a reason)
  ID  STATUS    REASON               TRIES  KEY                          DETAIL
   1  REPLAYED  VALIDATION_FAILED        0  -                            /ShipmentMessage/Shipment/Mode: 'TELEPORT' is not one of ['A
   2  DISCARDED PERMANENT_REJECTION      1  PARTNER-A:MSG-1005           ERP returned HTTP 422: {"error": "simulated business rule re
   3  REPLAYED  RETRIES_EXHAUSTED        4  PARTNER-A:MSG-1006           ERP returned HTTP 503

=== Scenario 7: sender redelivers an old message (duplicate) and sends an out-of-date update (stale) ===
{"ts": "2026-11-04T23:05:12.171+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-dup", "key": "PARTNER-A:MSG-1001", "status": "DUPLICATE", "source": "http:127.0.0.1"}
inbound HTTP 200: outcome=DUPLICATE_SKIPPED detail='already delivered'
{"ts": "2026-11-04T23:05:12.173+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-seq2", "key": "PARTNER-A:MSG-2002", "status": "NEW", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:12.177+00:00", "level": "INFO", "event": "delivered", "correlation_id": "demo-seq2", "key": "PARTNER-A:MSG-2002", "reference": "SIM-ORD-2000", "attempts": 1, "erp_response": "ACCEPTED"}
{"ts": "2026-11-04T23:05:12.180+00:00", "level": "INFO", "event": "idempotency_check", "correlation_id": "demo-seq1", "key": "PARTNER-A:MSG-2001", "status": "NEW", "source": "http:127.0.0.1"}
{"ts": "2026-11-04T23:05:12.182+00:00", "level": "WARNING", "event": "stale_skipped", "correlation_id": "demo-seq1", "reference": "SIM-ORD-2000", "sequence": 1, "newest_delivered": 2}
late update, inbound HTTP 200: outcome=STALE_SKIPPED detail='newer sequence already delivered'

=== Scenario 8: reconciliation, before and after the ERP 'loses' a record (simulated) ===
Reconciliation report (2026-11-04T23:05:12+00:00)
  delivered according to middleware : 6
  records held by ERP               : 6
  message states                    : {'DEAD_LETTERED': 1, 'DELIVERED': 6, 'STALE': 1}
  open dead letters                 : 0
  result: CLEAN

(simulated: ERP forgot PARTNER-A:MSG-1001)

Reconciliation report (2026-11-04T23:05:12+00:00)
  delivered according to middleware : 6
  records held by ERP               : 5
  message states                    : {'DEAD_LETTERED': 1, 'DELIVERED': 6, 'STALE': 1}
  open dead letters                 : 0
  MISSING_IN_ERP (we say delivered, ERP has no record):
    - {'key': 'PARTNER-A:MSG-1001', 'reference': 'SIM-OK-1001'}
  result: DISCREPANCIES FOUND

--- metrics ---
{
  "dead_lettered_total": 3,
  "delivered_total": 6,
  "delivery_attempts_total": 14,
  "duplicates_skipped_total": 1,
  "messages_received_total": 11,
  "replays_succeeded_total": 2,
  "retries_total": 6,
  "stale_skipped_total": 1,
  "validation_failures_total": 1
}
```
