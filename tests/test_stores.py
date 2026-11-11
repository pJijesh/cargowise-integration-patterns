from mini_middleware.dlq import RETRIES_EXHAUSTED, DeadLetterStore
from mini_middleware.idempotency import BeginStatus, IdempotencyStore


def test_idempotency_lifecycle(tmp_path):
    now = [1000.0]
    store = IdempotencyStore(tmp_path / "a.db", lease_seconds=60, clock=lambda: now[0])

    assert store.begin("k1", "fp", "c1") is BeginStatus.NEW
    assert store.begin("k1", "fp", "c2") is BeginStatus.IN_FLIGHT  # lease still valid

    now[0] += 61  # worker died; lease expired
    assert [r["idempotency_key"] for r in store.stuck()] == ["k1"]
    assert store.begin("k1", "fp", "c3") is BeginStatus.RESUMED

    store.mark_delivered("k1", attempts=2, reference="REF-1", sequence=5)
    assert store.begin("k1", "fp", "c4") is BeginStatus.DUPLICATE
    assert store.get("k1")["attempts"] == 2
    assert store.stuck() == []


def test_same_key_with_different_content_is_a_conflict(tmp_path):
    store = IdempotencyStore(tmp_path / "b.db")
    store.begin("k", "fp-1", "c")
    store.mark_delivered("k", 1, "REF", 1)
    assert store.begin("k", "fp-2", "c") is BeginStatus.CONFLICT


def test_dead_lettered_key_can_be_resumed_and_forgotten(tmp_path):
    store = IdempotencyStore(tmp_path / "c.db")
    store.begin("k", "fp", "c")
    store.mark_dead("k", 4, "down")
    assert store.begin("k", "fp", "c") is BeginStatus.RESUMED
    store.mark_dead("k", 1, "down")
    store.forget_dead("k")
    assert store.begin("k", "other-fp", "c") is BeginStatus.NEW


def test_sequence_guard_only_advances(tmp_path):
    store = IdempotencyStore(tmp_path / "d.db")
    assert not store.is_stale("REF", 1)
    for key, seq in [("a", 3), ("b", 5), ("c", 4)]:
        store.begin(key, key, "c")
        store.mark_delivered(key, 1, "REF", seq)
    assert store.last_sequence("REF") == 5
    assert store.is_stale("REF", 5) and store.is_stale("REF", 2)
    assert not store.is_stale("REF", 6)
    assert not store.is_stale("OTHER", 1)


def test_dead_letter_store_lifecycle(tmp_path):
    dlq = DeadLetterStore(tmp_path / "e.db")
    i = dlq.add(key="k", correlation_id="c", source="t", reason=RETRIES_EXHAUSTED,
                detail="down", attempts=4, payload="<x/>")
    assert [r["id"] for r in dlq.list()] == [i] and dlq.count_open() == 1
    dlq.record_replay_failure(i, correlation_id="c2", reason=RETRIES_EXHAUSTED, detail="still down", attempts=4)
    row = dlq.get(i)
    assert row["replay_count"] == 1 and row["attempts"] == 8 and row["status"] == "DEAD"
    dlq.mark_replayed(i, "DELIVERED", "c3")
    assert dlq.list() == [] and dlq.get(i)["status"] == "REPLAYED"
    j = dlq.add(key=None, correlation_id="c", source="t", reason="X", detail="d", attempts=0, payload="p")
    dlq.discard(j, "not needed")
    assert dlq.get(j)["status"] == "DISCARDED" and dlq.count_open() == 0
