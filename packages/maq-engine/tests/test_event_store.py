"""EventStore: append-only, hash-chained, durable."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from maq_core.events import Event
from maq_engine import GENESIS_HASH, EventStore
from maq_engine.store import chain_hash

T0 = datetime(2026, 11, 3, 12, tzinfo=UTC)


class Noted(Event):
    kind: Literal["test.noted"] = "test.noted"
    text: str


class Other(Event):
    kind: Literal["test.other"] = "test.other"


def fill(store: EventStore, count: int = 5) -> None:
    store.append_many(
        Noted(ts=T0 + timedelta(minutes=i), text=f"n{i}", cycle_id=f"c{i % 2}")
        for i in range(count)
    )


@contextmanager
def raw(store: EventStore) -> Iterator[sqlite3.Connection]:
    """A second connection that can bypass the triggers, like an attacker would."""
    db = sqlite3.connect(store.path, isolation_level=None)
    try:
        db.execute("DROP TRIGGER events_no_update")
        db.execute("DROP TRIGGER events_no_delete")
        yield db
    finally:
        db.close()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    with EventStore(tmp_path / "events.db") as opened:
        yield opened


def test_append_and_read_back(store: EventStore) -> None:
    first = store.append(Noted(ts=T0, text="hello"))
    assert first.seq == 1
    assert first.prev_hash == GENESIS_HASH
    assert first.hash == chain_hash(GENESIS_HASH, first.payload)
    assert first.event == Noted(ts=T0, text="hello", event_id=first.event.event_id)
    fill(store)
    assert len(store) == 6
    assert store.head() == (6, list(store.read())[-1].hash)


def test_filters(store: EventStore) -> None:
    fill(store, 6)
    store.append(Other(ts=T0 + timedelta(hours=1)))
    assert [e.kind for e in store.events(kinds=["test.other"])] == ["test.other"]
    assert len(store.events(cycle_id="c1")) == 3
    assert len(store.events(since=T0 + timedelta(minutes=4))) == 3
    assert [s.seq for s in store.read(after_seq=5)] == [6, 7]
    assert len(store.events(limit=2)) == 2


def test_empty_store(store: EventStore) -> None:
    assert store.head() == (0, GENESIS_HASH)
    report = store.verify()
    assert report.ok and report.checked == 0 and report.head == GENESIS_HASH


def test_history_cannot_be_rewritten_through_the_store(store: EventStore) -> None:
    fill(store)
    db = sqlite3.connect(store.path, isolation_level=None)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute("UPDATE events SET payload = '{}' WHERE seq = 2")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute("DELETE FROM events WHERE seq = 2")
    finally:
        db.close()


def test_verify_finds_a_changed_record(store: EventStore) -> None:
    fill(store)
    assert store.verify().ok
    with raw(store) as db:
        db.execute("UPDATE events SET payload = replace(payload, 'n2', 'forged') WHERE seq = 3")
    report = store.verify()
    assert not report.ok
    assert report.first_bad_seq == 3
    assert report.checked == 2
    assert "content" in report.problem


def test_verify_finds_a_deleted_record(store: EventStore) -> None:
    fill(store)
    with raw(store) as db:
        db.execute("DELETE FROM events WHERE seq = 2")
    report = store.verify()
    assert not report.ok
    assert report.first_bad_seq == 3
    assert "previous hash" in report.problem


def test_rehashing_the_tail_is_caught_by_the_anchor(store: EventStore) -> None:
    fill(store)
    _, anchor = store.head()
    with raw(store) as db:
        db.execute("DELETE FROM events WHERE seq = 5")  # drop the last record: chain stays valid
    assert store.verify().ok
    report = store.verify(expected_head=anchor)
    assert not report.ok
    assert "anchor" in report.problem


def test_append_many_is_atomic(store: EventStore) -> None:
    fill(store, 2)
    duplicate = Noted(ts=T0, text="dup")
    with pytest.raises(sqlite3.IntegrityError):
        store.append_many([Noted(ts=T0, text="fine"), duplicate, duplicate])
    assert len(store) == 2
    assert store.verify().ok


def test_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.db"
    with EventStore(path) as store:
        fill(store, 3)
        head = store.head()
    with EventStore(path) as reopened:
        assert reopened.head() == head
        reopened.append(Noted(ts=T0, text="after restart"))
        assert reopened.verify().ok
        assert reopened.verify().checked == 4


def test_in_memory_store() -> None:
    with EventStore() as store:
        fill(store, 2)
        assert store.verify().ok
