"""Action ledger: every prompt, decision, answer, job and notification BOSSK touches.

SQLite, one file, append-only in practice. "What did you do last night?" is a query here.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,            -- prompt | decision | ask | answer | job | notify | session | pause | health
    machine TEXT,
    session_id TEXT,
    tool TEXT,
    tier INTEGER,
    rule_id TEXT,
    outcome TEXT,                  -- allow | deny | ask | pending | yes | no | ...
    summary TEXT,
    data TEXT                      -- json blob with the full payload
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, ts);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def record(self, kind: str, *, machine: str | None = None, session_id: str | None = None,
               tool: str | None = None, tier: int | None = None, rule_id: str | None = None,
               outcome: str | None = None, summary: str = "", data: dict[str, Any] | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, kind, machine, session_id, tool, tier, rule_id, outcome, summary, data)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), kind, machine, session_id, tool, tier, rule_id, outcome, summary[:500],
                 json.dumps(data or {}, default=str)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def query(self, *, since: float | None = None, until: float | None = None, kind: str | None = None,
              session_id: str | None = None, machine: str | None = None, limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if since is not None:
            sql += " AND ts >= ?"; args.append(since)
        if until is not None:
            sql += " AND ts <= ?"; args.append(until)
        if kind:
            sql += " AND kind = ?"; args.append(kind)
        if session_id:
            sql += " AND session_id = ?"; args.append(session_id)
        if machine:
            sql += " AND machine = ?"; args.append(machine)
        sql += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def prompts(self, since: float) -> Iterator[dict]:
        """Every prompt event since `since`, oldest first. Used by rule replay."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE kind='prompt' AND ts >= ? ORDER BY ts ASC", (since,)).fetchall()
        for r in rows:
            yield self._row(r)

    def counts(self, since: float) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind || ':' || COALESCE(outcome,'') AS k, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY k",
                (since,)).fetchall()
        return {r["k"]: r["n"] for r in rows}

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["data"] = json.loads(d.get("data") or "{}")
        except json.JSONDecodeError:
            d["data"] = {}
        return d

    def close(self) -> None:
        with self._lock:
            self._conn.close()
