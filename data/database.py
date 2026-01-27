"""SQLite persistence for cycle outputs and analytics."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                final_action TEXT,
                payload TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id INTEGER,
                timestamp TEXT,
                model TEXT,
                symbol TEXT,
                action TEXT,
                confidence REAL,
                payload TEXT,
                FOREIGN KEY(cycle_id) REFERENCES cycles(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                profit REAL,
                volume REAL,
                payload TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                metric_type TEXT,
                payload TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deepseek_aux (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                task_type TEXT,
                payload TEXT
            )
            """
        )


def store_cycle(result: Dict[str, Any], path: Path) -> Optional[int]:
    init_db(path)
    cycle_payload = json.dumps(result)
    timestamp = result.get("snapshot_meta", {}).get("timestamp")
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    symbol = result.get("snapshot_meta", {}).get("symbol")
    final_action = result.get("final_signal", {}).get("action")

    with sqlite3.connect(path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO cycles (timestamp, symbol, final_action, payload)
            VALUES (?, ?, ?, ?)
            """,
            (timestamp, symbol, final_action, cycle_payload),
        )
        cycle_id = cur.lastrowid

        for signal in result.get("signals", []) or []:
            cur.execute(
                """
                INSERT INTO signals (cycle_id, timestamp, model, symbol, action, confidence, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    signal.get("timestamp"),
                    signal.get("model"),
                    signal.get("symbol"),
                    signal.get("action"),
                    signal.get("confidence"),
                    json.dumps(signal),
                ),
            )
        conn.commit()
    return cycle_id


def store_trades(trades: Iterable[Dict[str, Any]], path: Path) -> None:
    init_db(path)
    with sqlite3.connect(path) as conn:
        for trade in trades:
            conn.execute(
                """
                INSERT INTO trades (timestamp, symbol, profit, volume, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    trade.get("time"),
                    trade.get("symbol"),
                    trade.get("profit"),
                    trade.get("volume"),
                    json.dumps(trade),
                ),
            )
        conn.commit()


def store_performance(metric_type: str, payload: Dict[str, Any], path: Path) -> None:
    init_db(path)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO performance (timestamp, metric_type, payload)
            VALUES (?, ?, ?)
            """,
            (timestamp, metric_type, json.dumps(payload)),
        )
        conn.commit()


def store_deepseek_aux(task_type: str, payload: Dict[str, Any], path: Path) -> None:
    init_db(path)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO deepseek_aux (timestamp, task_type, payload)
            VALUES (?, ?, ?)
            """,
            (timestamp, task_type, json.dumps(payload)),
        )
        conn.commit()
