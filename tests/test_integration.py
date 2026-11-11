"""End-to-end tests: real pipeline, real SQLite, real HTTP, against the mock ERP."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from mini_middleware.adapters.folder import FolderAdapter
from mini_middleware.adapters.http_inbound import make_server
from mini_middleware.cli import main as cli_main
from mini_middleware.dlq import (
    KEY_REUSE_CONFLICT,
    PERMANENT_REJECTION,
    RETRIES_EXHAUSTED,
    VALIDATION_FAILED,
)
from mini_middleware.pipeline import Outcome
from mini_middleware.reconcile import reconcile
from mini_middleware.samples import make_message


def test_happy_path_delivers_once(pipeline, erp):
    r = pipeline.process(make_message("M1", "SIM-OK-1"), "test", correlation_id="cid-1")
    assert r.outcome is Outcome.DELIVERED and r.attempts == 1 and r.correlation_id == "cid-1"
    assert [rec["key"] for rec in erp.records()] == ["PARTNER-A:M1"]
    assert erp.records()[0]["reference"] == "SIM-OK-1"


def test_transient_failures_are_retried_with_backoff(pipeline, erp, sleeps):
    r = pipeline.process(make_message("M1", "SIM-FAIL500-2"), "test")
    assert r.outcome is Outcome.DELIVERED and r.attempts == 3
    assert len(sleeps) == 2 and all(0 <= s <= 0.05 for s in sleeps)
    assert len(erp.records()) == 1


def test_throttling_honours_retry_after(pipeline, sleeps):
    r = pipeline.process(make_message("M1", "SIM-THROTTLE-1"), "test")
    assert r.outcome is Outcome.DELIVERED and r.attempts == 2 and len(sleeps) == 1


def test_timeout_then_success(pipeline, erp):
    r = pipeline.process(make_message("M1", "SIM-TIMEOUT-1"), "test")
    assert r.outcome is Outcome.DELIVERED and r.attempts == 2
    assert len(erp.records()) == 1


def test_lost_acknowledgement_does_not_create_a_duplicate_in_the_erp(pipeline, erp):
    r = pipeline.process(make_message("M1", "SIM-LOSTACK-1"), "test")
    assert r.outcome is Outcome.DELIVERED and r.attempts == 2
    assert r.erp_response == "ALREADY_PROCESSED"
    assert len(erp.records()) == 1


def test_sender_redelivery_is_skipped(pipeline, erp):
    msg = make_message("M1", "SIM-OK-1")
    assert pipeline.process(msg, "test").outcome is Outcome.DELIVERED
    again = pipeline.process(msg, "test")
    assert again.outcome is Outcome.DUPLICATE_SKIPPED
    assert len(erp.records()) == 1
    assert pipeline.metrics.snapshot()["duplicates_skipped_total"] == 1


def test_same_key_different_content_goes_to_the_dlq(pipeline):
    pipeline.process(make_message("M1", "SIM-OK-1"), "test")
    r = pipeline.process(make_message("M1", "SIM-OK-1", packages=99), "test")
    assert r.outcome is Outcome.DEAD_LETTERED and r.reason == KEY_REUSE_CONFLICT


def test_stale_update_is_not_delivered(pipeline, erp):
    pipeline.process(make_message("M2", "SIM-OK-2", sequence=2, status="ARRIVED"), "test")
    r = pipeline.process(make_message("M1", "SIM-OK-2", sequence=1), "test")
    assert r.outcome is Outcome.STALE_SKIPPED
    assert len(erp.records()) == 1


def test_invalid_message_is_dead_lettered_without_calling_the_erp(pipeline, erp):
    r = pipeline.process(make_message("M1", "SIM-OK-1", mode="TELEPORT"), "test")
    assert r.outcome is Outcome.DEAD_LETTERED and r.reason == VALIDATION_FAILED
    assert r.attempts == 0 and erp.records() == []
    assert pipeline.dlq.get(r.dlq_id)["status"] == "DEAD"


def test_permanent_rejection_is_not_retried(pipeline, sleeps):
    r = pipeline.process(make_message("M1", "SIM-REJECT-1"), "test")
    assert r.outcome is Outcome.DEAD_LETTERED and r.reason == PERMANENT_REJECTION
    assert r.attempts == 1 and sleeps == []


def test_outage_exhausts_retries_then_replay_succeeds_after_recovery(pipeline, erp):
    r = pipeline.process(make_message("M1", "SIM-OUTAGE-1"), "test")
    assert r.outcome is Outcome.DEAD_LETTERED and r.reason == RETRIES_EXHAUSTED and r.attempts == 4

    still_down = pipeline.replay(r.dlq_id)
    assert still_down.outcome is Outcome.DEAD_LETTERED
    row = pipeline.dlq.get(r.dlq_id)
    assert row["status"] == "DEAD" and row["replay_count"] == 1 and len(pipeline.dlq.list()) == 1

    erp.recover()
    ok = pipeline.replay(r.dlq_id)
    assert ok.outcome is Outcome.DELIVERED
    assert pipeline.dlq.get(r.dlq_id)["status"] == "REPLAYED"
    assert len(erp.records()) == 1
    with pytest.raises(ValueError):
        pipeline.replay(r.dlq_id)  # already resolved


def test_replay_with_corrected_payload(pipeline, erp):
    bad = pipeline.process(make_message("M1", "SIM-OK-1", mode="TELEPORT"), "test")
    fixed = pipeline.replay(bad.dlq_id, payload_override=make_message("M1", "SIM-OK-1"))
    assert fixed.outcome is Outcome.DELIVERED
    assert "TELEPORT" in pipeline.dlq.get(bad.dlq_id)["payload"]  # original kept for audit
    assert "corrected" in pipeline.dlq.get(bad.dlq_id)["resolution"]


def test_corrected_payload_may_reuse_the_key_of_a_rejected_message(pipeline, erp):
    rejected = pipeline.process(make_message("M1", "SIM-REJECT-1"), "test")
    corrected = pipeline.replay(rejected.dlq_id, payload_override=make_message("M1", "SIM-OK-1", packages=3))
    assert corrected.outcome is Outcome.DELIVERED


def test_crashed_worker_is_resumed_after_lease_expiry(config, erp):
    import random

    from mini_middleware.idempotency import IdempotencyStore
    from mini_middleware.metrics import Metrics
    from mini_middleware.pipeline import Pipeline
    from mini_middleware.retry import RetryPolicy
    from mini_middleware.transformer import fingerprint

    now = [1000.0]
    store = IdempotencyStore(config.db_path, lease_seconds=30, clock=lambda: now[0])
    p = Pipeline(config, store=store, metrics=Metrics(),
                 retry=RetryPolicy(sleep=lambda s: None, rng=random.Random(1)))
    msg = make_message("M1", "SIM-OK-1")
    store.begin("PARTNER-A:M1", fingerprint(msg), "dead-worker")  # claimed, never finished
    assert p.process(msg, "test").outcome is Outcome.DUPLICATE_SKIPPED  # lease still held
    now[0] += 31
    assert p.process(msg, "test").outcome is Outcome.DELIVERED


def test_reconciliation_detects_a_missing_erp_record(pipeline, erp):
    pipeline.process(make_message("M1", "SIM-OK-1"), "test")
    pipeline.process(make_message("M2", "SIM-OK-2"), "test")
    assert reconcile(pipeline.store, pipeline.dlq, pipeline.client).clean
    erp.forget("PARTNER-A:M2")
    report = reconcile(pipeline.store, pipeline.dlq, pipeline.client)
    assert not report.clean
    assert [r["key"] for r in report.missing_in_erp] == ["PARTNER-A:M2"]


def test_reconciliation_flags_open_dead_letters_and_unexpected_erp_records(pipeline, erp):
    pipeline.process(make_message("M1", "SIM-REJECT-1"), "test")
    erp._store("SOMEONE-ELSE:X", "SIM-OK-9", 10)
    report = reconcile(pipeline.store, pipeline.dlq, pipeline.client)
    assert report.open_dead_letters == 1
    assert [r["key"] for r in report.unexpected_in_erp] == ["SOMEONE-ELSE:X"]


def test_erp_rejects_a_wrong_token_as_permanent(config, erp):
    import dataclasses

    from mini_middleware.metrics import Metrics
    from mini_middleware.pipeline import Pipeline

    p = Pipeline(dataclasses.replace(config, erp_token="wrong"), metrics=Metrics())
    r = p.process(make_message("M1", "SIM-OK-1"), "test")
    assert r.outcome is Outcome.DEAD_LETTERED and r.reason == PERMANENT_REJECTION
    assert "401" in r.detail


# ---- adapters ----------------------------------------------------------------------


def test_folder_adapter_moves_files_by_outcome(pipeline, tmp_path):
    folder = FolderAdapter(tmp_path / "drop", pipeline, settle_seconds=0)
    (folder.inbox / "a.xml").write_text(make_message("M1", "SIM-OK-1"))
    (folder.inbox / "b.xml").write_text(make_message("M2", "SIM-OK-2", mode="TELEPORT"))
    (folder.inbox / "c.txt").write_text("ignored")
    (folder.inbox / "d.xml.tmp").write_text("half written")
    results = dict(folder.scan_once())
    assert results["a.xml"].outcome is Outcome.DELIVERED
    assert results["b.xml"].outcome is Outcome.DEAD_LETTERED
    assert (folder.processed / "a.xml").exists() and (folder.failed / "b.xml").exists()
    assert (folder.inbox / "c.txt").exists() and (folder.inbox / "d.xml.tmp").exists()
    assert list(folder.processing.iterdir()) == []


def test_folder_adapter_respects_the_settle_window(pipeline, tmp_path):
    folder = FolderAdapter(tmp_path / "drop", pipeline, settle_seconds=60)
    (folder.inbox / "a.xml").write_text(make_message("M1", "SIM-OK-1"))
    assert folder.scan_once() == []
    assert (folder.inbox / "a.xml").exists()


def test_folder_adapter_recovers_orphans_and_rejects_oversize(pipeline, tmp_path):
    folder = FolderAdapter(tmp_path / "drop", pipeline, settle_seconds=0, max_bytes=100)
    (folder.processing / "stranded.xml").write_text("x" * 10)
    assert folder.recover_orphans() == 1 and (folder.inbox / "stranded.xml").exists()
    folder.scan_once()
    assert (folder.failed / "stranded.xml").exists()  # 10 bytes is fine, but it is not valid XML...
    (folder.inbox / "big.xml").write_text("y" * 500)
    folder.scan_once()
    assert (folder.failed / "big.xml").exists()
    assert pipeline.metrics.snapshot()["oversize_rejected_total"] == 1


@pytest.fixture
def inbound(pipeline):
    server = make_server(pipeline, port=0, token="inbound-secret")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=body.encode(), method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read()), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), exc.headers


AUTH = {"Authorization": "Bearer inbound-secret"}


def test_http_adapter_status_codes_and_correlation_header(inbound):
    status, body, headers = _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1"),
                                  {**AUTH, "X-Correlation-Id": "from-sender-1"})
    assert status == 200 and body["outcome"] == "DELIVERED"
    assert headers["X-Correlation-Id"] == "from-sender-1"

    status, body, _ = _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1"), AUTH)
    assert status == 200 and body["outcome"] == "DUPLICATE_SKIPPED"

    status, body, _ = _post(f"{inbound}/messages", make_message("M2", "SIM-OK-2", mode="X"), AUTH)
    assert status == 422 and body["reason"] == VALIDATION_FAILED

    status, body, _ = _post(f"{inbound}/messages", make_message("M3", "SIM-REJECT-3"), AUTH)
    assert status == 202 and body["reason"] == PERMANENT_REJECTION

    status, body, _ = _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1", packages=7), AUTH)
    assert status == 409 and body["reason"] == KEY_REUSE_CONFLICT


def test_http_adapter_requires_the_token_and_sanitises_correlation_ids(inbound):
    status, _, _ = _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1"))
    assert status == 401
    status, _, _ = _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1"),
                         {"Authorization": "Bearer wrong"})
    assert status == 401
    status, body, headers = _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1"),
                                  {**AUTH, "X-Correlation-Id": "bad id with spaces"})
    assert status == 200 and headers["X-Correlation-Id"] == body["correlation_id"] != "bad id with spaces"


def test_http_adapter_health_and_metrics(inbound):
    with urllib.request.urlopen(f"{inbound}/healthz") as resp:
        assert json.loads(resp.read()) == {"status": "ok"}
    _post(f"{inbound}/messages", make_message("M1", "SIM-OK-1"), AUTH)
    with urllib.request.urlopen(f"{inbound}/metrics") as resp:
        text = resp.read().decode()
    assert "mm_delivered_total 1" in text


# ---- CLI ---------------------------------------------------------------------------


def test_cli_ingest_dlq_replay_and_reconcile(tmp_path, erp, capsys, monkeypatch):
    monkeypatch.setenv("MM_ERP_TOKEN", "test-token")
    monkeypatch.setenv("MM_ERP_TIMEOUT", "0.3")
    monkeypatch.setenv("MM_BACKOFF_BASE", "0.001")
    monkeypatch.setenv("MM_BACKOFF_CAP", "0.002")
    base = ["--db", str(tmp_path / "cli.db"), "--erp-url", erp.url]
    good, outage = tmp_path / "good.xml", tmp_path / "outage.xml"
    good.write_text(make_message("M1", "SIM-OK-1"))
    outage.write_text(make_message("M2", "SIM-OUTAGE-2"))

    assert cli_main(base + ["ingest", str(good)]) == 0
    assert cli_main(base + ["ingest", str(outage)]) == 1
    capsys.readouterr()

    assert cli_main(base + ["dlq", "list"]) == 0
    assert "RETRIES_EXHAUSTED" in capsys.readouterr().out
    assert cli_main(base + ["reconcile"]) == 2  # an open dead letter is a discrepancy
    capsys.readouterr()

    erp.recover()
    assert cli_main(base + ["dlq", "replay", "1"]) == 0
    assert cli_main(base + ["dlq", "list", "--status", "ALL"]) == 0
    assert "REPLAYED" in capsys.readouterr().out
    assert cli_main(base + ["reconcile", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True
    assert cli_main(base + ["dlq", "replay", "99"]) == 1
