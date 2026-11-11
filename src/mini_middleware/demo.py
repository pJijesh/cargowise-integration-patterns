"""Scripted end-to-end demonstration against the mock ERP.

Everything runs in-process using a temporary directory. Timings are shortened so the
whole run takes a few seconds.
"""

from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from .adapters.folder import FolderAdapter
from .adapters.http_inbound import make_server
from .config import Config
from .logging_setup import configure_logging
from .metrics import Metrics
from .mock_erp import MockErp
from .pipeline import Pipeline
from .reconcile import reconcile
from .retry import RetryPolicy
from .samples import make_message


def _heading(n: int, title: str) -> None:
    print(f"\n=== Scenario {n}: {title} ===", flush=True)


def _post(url: str, body: str, cid: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=body.encode(), method="POST",
        headers={"Content-Type": "application/xml", "X-Correlation-Id": cid},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def run_demo() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="mm-demo-"))
    configure_logging("INFO", sys.stdout)
    erp = MockErp(token="demo-token", slow_seconds=1.0).start()
    cfg = Config(
        db_path=workdir / "middleware.db",
        erp_url=erp.url,
        erp_token="demo-token",
        erp_timeout=0.4,
        max_attempts=4,
    )
    metrics = Metrics()
    retry = RetryPolicy(max_attempts=4, base=0.05, cap=0.4, rng=random.Random(7))
    pipeline = Pipeline(cfg, retry=retry, metrics=metrics)
    folder = FolderAdapter(workdir / "drop", pipeline, settle_seconds=0)
    server = make_server(pipeline, port=0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    inbound = f"http://127.0.0.1:{server.server_address[1]}/messages"

    print("mini_middleware demo (illustrative messages, mock ERP, temporary database)")
    print(f"working directory: {workdir}")

    try:
        _heading(1, "happy path via folder drop")
        (folder.inbox / "ok-1001.xml").write_text(make_message("MSG-1001", "SIM-OK-1001"))
        folder.scan_once()

        _heading(2, "transient ERP failures are retried (HTTP endpoint; 500, 500, then success)")
        status, body = _post(inbound, make_message("MSG-1002", "SIM-FAIL500-2"), "demo-retry")
        print(f"inbound HTTP {status}: outcome={body['outcome']} attempts={body['attempts']}")

        _heading(3, "lost acknowledgement: ERP stored it but the reply timed out; the retry gets 409")
        (folder.inbox / "lostack-1003.xml").write_text(make_message("MSG-1003", "SIM-LOSTACK-1"))
        folder.scan_once()

        _heading(4, "poison messages go straight to the dead-letter queue, no retries")
        bad = make_message("MSG-1004", "SIM-OK-1004", mode="TELEPORT")
        status, body = _post(inbound, bad, "demo-invalid")
        print(f"inbound HTTP {status}: outcome={body['outcome']} reason={body['reason']}")
        status, body = _post(inbound, make_message("MSG-1005", "SIM-REJECT-1005"), "demo-reject")
        print(f"inbound HTTP {status}: outcome={body['outcome']} reason={body['reason']}")

        _heading(5, "ERP outage: retries are exhausted and the message is dead-lettered")
        status, body = _post(inbound, make_message("MSG-1006", "SIM-OUTAGE-1006"), "demo-outage")
        print(f"inbound HTTP {status}: outcome={body['outcome']} reason={body['reason']} attempts={body['attempts']}")

        print("\n--- dead-letter queue ---")
        from .cli import _dlq_table

        print(_dlq_table(pipeline.dlq.list("DEAD")))

        _heading(6, "ERP recovers; operator replays the outage victim; the invalid one is corrected and replayed")
        erp.recover()
        outage_id = next(r["id"] for r in pipeline.dlq.list("DEAD") if r["reason"] == "RETRIES_EXHAUSTED")
        result = pipeline.replay(outage_id, correlation_id="demo-replay-1")
        print(f"replay {outage_id}: {result.outcome.value} attempts={result.attempts}")
        invalid_id = next(r["id"] for r in pipeline.dlq.list("DEAD") if r["reason"] == "VALIDATION_FAILED")
        fixed = make_message("MSG-1004", "SIM-OK-1004", mode="SEA")
        result = pipeline.replay(invalid_id, payload_override=fixed, correlation_id="demo-replay-2")
        print(f"replay {invalid_id} with corrected payload: {result.outcome.value}")
        reject_id = next(r["id"] for r in pipeline.dlq.list("DEAD") if r["reason"] == "PERMANENT_REJECTION")
        pipeline.dlq.discard(reject_id, "rejection confirmed as correct with the sender; no resend needed")
        print(f"discarded {reject_id} after review (a human decision, recorded with a reason)")
        from .cli import _dlq_table as _table

        print(_table(pipeline.dlq.list(None)))

        _heading(7, "sender redelivers an old message (duplicate) and sends an out-of-date update (stale)")
        status, body = _post(inbound, make_message("MSG-1001", "SIM-OK-1001"), "demo-dup")
        print(f"inbound HTTP {status}: outcome={body['outcome']} detail={body['detail']!r}")
        _post(inbound, make_message("MSG-2002", "SIM-ORD-2000", sequence=2, status="ARRIVED"), "demo-seq2")
        status, body = _post(inbound, make_message("MSG-2001", "SIM-ORD-2000", sequence=1), "demo-seq1")
        print(f"late update, inbound HTTP {status}: outcome={body['outcome']} detail={body['detail']!r}")

        _heading(8, "reconciliation, before and after the ERP 'loses' a record (simulated)")
        print(reconcile(pipeline.store, pipeline.dlq, pipeline.client).render_text())
        lost_key = "PARTNER-A:MSG-1001"
        erp.forget(lost_key)
        print(f"\n(simulated: ERP forgot {lost_key})\n")
        print(reconcile(pipeline.store, pipeline.dlq, pipeline.client).render_text())

        print("\n--- metrics ---")
        print(json.dumps(metrics.snapshot(), indent=2))
    finally:
        server.shutdown()
        erp.stop()
        shutil.rmtree(workdir, ignore_errors=True)
    return 0
