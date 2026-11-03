"""The append-only, hash-chained event log (SQLite in WAL mode).

Each record stores the event's canonical JSON and
``hash = sha256(previous hash + "\\n" + json)``. Changing, deleting or
reordering any record breaks every hash after it, which ``verify`` reports.
Removing records from the *end* leaves a valid but shorter chain: anchor
``head()`` somewhere else (a daily report, a commit) and pass it to
``verify(expected_head=...)`` to catch that too. Database triggers refuse
UPDATE and DELETE, so ordinary code cannot rewrite history by accident.

Writes are durable when ``append`` returns (``synchronous=FULL``): the order
manager relies on that to persist an order before sending it.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from maq_core.events import Event, canonical_json, parse_event

GENESIS_HASH = "0" * 64
DEFAULT_EVENT_DB = Path("logs") / "events.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id  TEXT NOT NULL UNIQUE,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    cycle_id  TEXT,
    payload   TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind);
CREATE INDEX IF NOT EXISTS events_cycle ON events (cycle_id);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'the event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'the event log is append-only'); END;
"""


def _ts(value: datetime) -> str:
    """Fixed-width UTC text, so timestamps sort correctly as strings."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def chain_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{payload}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredEvent:
    seq: int
    hash: str
    prev_hash: str
    payload: str

    @property
    def event(self) -> Event:
        return parse_event(self.payload)


@dataclass(frozen=True, slots=True)
class VerifyReport:
    ok: bool
    checked: int
    head: str
    first_bad_seq: int | None = None
    problem: str = ""


class EventStore:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ writing
    def append(self, event: Event) -> StoredEvent:
        return self.append_many([event])[0]

    def append_many(self, events: Iterable[Event]) -> list[StoredEvent]:
        """Append events atomically, in order."""
        stored: list[StoredEvent] = []
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT hash FROM events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                prev = row[0] if row else GENESIS_HASH
                for event in events:
                    payload = canonical_json(event)
                    digest = chain_hash(prev, payload)
                    cursor = self._db.execute(
                        "INSERT INTO events (event_id, ts, kind, cycle_id, payload, prev_hash, hash)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.event_id,
                            _ts(event.ts),
                            event.kind,
                            event.cycle_id,
                            payload,
                            prev,
                            digest,
                        ),
                    )
                    assert cursor.lastrowid is not None
                    stored.append(StoredEvent(cursor.lastrowid, digest, prev, payload))
                    prev = digest
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return stored

    # ------------------------------------------------------------------ reading
    def read(
        self,
        *,
        after_seq: int = 0,
        kinds: Sequence[str] | None = None,
        cycle_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[StoredEvent]:
        query = "SELECT seq, hash, prev_hash, payload FROM events WHERE seq > ?"
        params: list[object] = [after_seq]
        if kinds:
            query += f" AND kind IN ({', '.join('?' for _ in kinds)})"
            params.extend(kinds)
        if cycle_id is not None:
            query += " AND cycle_id = ?"
            params.append(cycle_id)
        if since is not None:
            query += " AND ts >= ?"
            params.append(_ts(since))
        query += " ORDER BY seq"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        for seq, digest, prev, payload in rows:
            yield StoredEvent(seq, digest, prev, payload)

    def events(
        self,
        *,
        after_seq: int = 0,
        kinds: Sequence[str] | None = None,
        cycle_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Decoded events; same filters as ``read``."""
        rows = self.read(
            after_seq=after_seq, kinds=kinds, cycle_id=cycle_id, since=since, limit=limit
        )
        return [stored.event for stored in rows]

    def head(self) -> tuple[int, str]:
        """(sequence number, hash) of the last record; (0, genesis) when empty."""
        with self._lock:
            row = self._db.execute(
                "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return (row[0], row[1]) if row else (0, GENESIS_HASH)

    def __len__(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    # ------------------------------------------------------------------ integrity
    def verify(self, expected_head: str | None = None) -> VerifyReport:
        prev = GENESIS_HASH
        checked = 0
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, payload, prev_hash, hash FROM events ORDER BY seq"
            ).fetchall()
        for seq, payload, stored_prev, stored_hash in rows:
            if stored_prev != prev:
                return VerifyReport(
                    False, checked, prev, seq, "previous hash does not match: a record before "
                    "this one was removed, reordered or changed"
                )  # fmt: skip
            if chain_hash(prev, payload) != stored_hash:
                return VerifyReport(False, checked, prev, seq, "content does not match its hash")
            prev = stored_hash
            checked += 1
        if expected_head is not None and expected_head != prev:
            return VerifyReport(
                False, checked, prev, None, "chain ends at a different hash than the anchor"
            )
        return VerifyReport(True, checked, prev)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
