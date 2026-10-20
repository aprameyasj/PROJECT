"""SQLite persistence layer.

One file (`.jarvis/jarvis.db`) holds everything the MVP needs:

    reminders   — time-based nudges fired by the scheduler
    events      — calendar entries
    tasks       — to-do items with project + priority
    facts       — long-term memory (profile, preferences, projects)
    messages    — short-term conversational memory
    audit       — every tool call, its permission and its verdict
    artifacts   — screenshots and other files JARVIS produced
    pending     — Level-2 actions waiting for confirmation

Swap this module for PostgreSQL later without touching the tools: the
interface is intentionally small and JSON-friendly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task         TEXT NOT NULL,
    remind_at    TEXT NOT NULL,          -- ISO-8601 local time
    timezone     TEXT DEFAULT '',
    recurrence   TEXT DEFAULT '',        -- '', 'daily', 'weekly', 'weekdays'
    status       TEXT DEFAULT 'pending', -- pending | fired | cancelled | snoozed
    channel      TEXT DEFAULT 'all',     -- all | notification | voice | dashboard
    notes        TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    fired_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status, remind_at);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    starts_at  TEXT NOT NULL,
    ends_at    TEXT,
    location   TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    status     TEXT DEFAULT 'scheduled',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_starts ON events(starts_at);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    status       TEXT DEFAULT 'todo',   -- todo | doing | done | cancelled
    priority     INTEGER DEFAULT 3,     -- 1 (high) .. 5 (low)
    project      TEXT DEFAULT '',
    due_at       TEXT,
    notes        TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    category   TEXT DEFAULT 'general',  -- profile | preference | project | goal | general
    confidence REAL DEFAULT 1.0,
    source     TEXT DEFAULT 'user',
    embedding  BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(key, category)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT DEFAULT '',
    tool_calls TEXT DEFAULT '',
    meta       TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    session_id TEXT DEFAULT '',
    turn_id    TEXT DEFAULT '',
    tool       TEXT NOT NULL,
    arguments  TEXT DEFAULT '{}',
    permission TEXT DEFAULT 'safe',
    decision   TEXT DEFAULT 'allow',    -- allow | confirm | block | error | ok
    reason     TEXT DEFAULT '',
    ok         INTEGER DEFAULT 1,
    message    TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    simulated  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,           -- screenshot | audio | file
    path       TEXT NOT NULL,
    meta       TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending (
    token      TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tool       TEXT NOT NULL,
    arguments  TEXT DEFAULT '{}',
    prompt     TEXT DEFAULT '',
    plan_id    TEXT,
    plan_index INTEGER DEFAULT 0,
    status     TEXT DEFAULT 'waiting',  -- waiting | approved | denied | expired
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    resolved_at REAL
);

CREATE TABLE IF NOT EXISTS plans (
    id         TEXT PRIMARY KEY,
    session_id TEXT DEFAULT '',
    title      TEXT DEFAULT '',
    goal       TEXT DEFAULT '',
    status     TEXT DEFAULT 'pending',
    spec       TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin, thread-safe SQLite wrapper."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # ------------------------------------------------------------------
    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def migrate(self) -> None:
        with self.cursor() as cur:
            cur.executescript(SCHEMA)
            cur.execute("PRAGMA user_version")
            version = cur.fetchone()[0]
            if version < SCHEMA_VERSION:
                cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._backfill_defaults()

    def _backfill_defaults(self) -> None:
        """Seed the user profile the first time JARVIS starts."""
        if not self.get_facts(category="profile"):
            defaults = [
                ("name", "", "profile"),
                ("preferred_language", "Python", "profile"),
                ("timezone", "", "profile"),
            ]
            for key, value, category in defaults:
                self.upsert_fact(key, value, category=category, source="bootstrap")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # reminders
    # ------------------------------------------------------------------
    def add_reminder(
        self,
        task: str,
        remind_at: datetime,
        *,
        recurrence: str = "",
        channel: str = "all",
        notes: str = "",
        timezone_name: str = "",
    ) -> dict[str, Any]:
        stamp = utcnow()
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO reminders (task, remind_at, timezone, recurrence, status, channel, notes,
                                          created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (task.strip(), remind_at.isoformat(timespec="seconds"), timezone_name, recurrence,
                 "pending", channel, notes, stamp, stamp),
            )
            reminder_id = int(cur.lastrowid or 0)
        return self.get_reminder(reminder_id)  # type: ignore[return-value]

    def get_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_reminders(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM reminders"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY remind_at ASC LIMIT ?"
        params.append(limit)
        with self.cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def update_reminder(self, reminder_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {"task", "remind_at", "status", "recurrence", "channel", "notes", "fired_at"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return self.get_reminder(reminder_id)
        fields["updated_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self.cursor() as cur:
            cur.execute(f"UPDATE reminders SET {assignments} WHERE id = ?", (*fields.values(), reminder_id))
        return self.get_reminder(reminder_id)

    def delete_reminder(self, reminder_id: int) -> bool:
        with self.cursor() as cur:
            cur.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            return cur.rowcount > 0

    def search_reminders(self, needle: str, limit: int = 25) -> list[dict[str, Any]]:
        like = f"%{needle.lower()}%"
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM reminders WHERE lower(task) LIKE ? OR lower(notes) LIKE ? "
                "ORDER BY remind_at ASC LIMIT ?",
                (like, like, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # calendar events
    # ------------------------------------------------------------------
    def add_event(self, title: str, starts_at: datetime, *, ends_at: datetime | None = None,
                  location: str = "", notes: str = "") -> dict[str, Any]:
        stamp = utcnow()
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO events (title, starts_at, ends_at, location, notes, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (title.strip(), starts_at.isoformat(timespec="seconds"),
                 ends_at.isoformat(timespec="seconds") if ends_at else None,
                 location, notes, "scheduled", stamp, stamp),
            )
            event_id = int(cur.lastrowid or 0)
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            row = cur.fetchone()
        return dict(row)

    def list_events(self, *, start: datetime | None = None, end: datetime | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE status != 'cancelled'"
        params: list[Any] = []
        if start:
            query += " AND starts_at >= ?"
            params.append(start.isoformat(timespec="seconds"))
        if end:
            query += " AND starts_at <= ?"
            params.append(end.isoformat(timespec="seconds"))
        query += " ORDER BY starts_at ASC LIMIT ?"
        params.append(limit)
        with self.cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def cancel_event(self, event_id: int) -> bool:
        with self.cursor() as cur:
            cur.execute("UPDATE events SET status='cancelled', updated_at=? WHERE id=?", (utcnow(), event_id))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # tasks
    # ------------------------------------------------------------------
    def add_task(self, title: str, *, project: str = "", priority: int = 3, due_at: datetime | None = None,
                 notes: str = "") -> dict[str, Any]:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks (title, status, priority, project, due_at, notes, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (title.strip(), "todo", int(priority), project,
                 due_at.isoformat(timespec="seconds") if due_at else None, notes, utcnow()),
            )
            task_id = int(cur.lastrowid or 0)
            cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            return dict(cur.fetchone())

    def list_tasks(self, status: str | None = None, project: str | None = None,
                   limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if project:
            clauses.append("lower(project) = ?")
            params.append(project.lower())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY status = 'done', priority ASC, id DESC LIMIT ?"
        params.append(limit)
        with self.cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def set_task_status(self, task_id: int, status: str) -> dict[str, Any] | None:
        completed = utcnow() if status == "done" else None
        with self.cursor() as cur:
            cur.execute("UPDATE tasks SET status=?, completed_at=? WHERE id=?", (status, completed, task_id))
            cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # long-term memory (facts)
    # ------------------------------------------------------------------
    def upsert_fact(self, key: str, value: str, *, category: str = "general", confidence: float = 1.0,
                    source: str = "user", embedding: bytes | None = None) -> dict[str, Any]:
        stamp = utcnow()
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO facts (key, value, category, confidence, source, embedding, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(key, category) DO UPDATE SET
                        value=excluded.value, confidence=excluded.confidence,
                        source=excluded.source, updated_at=excluded.updated_at""",
                (key.strip(), str(value), category, confidence, source, embedding, stamp, stamp),
            )
            cur.execute("SELECT * FROM facts WHERE key=? AND category=?", (key.strip(), category))
            return dict(cur.fetchone())

    def get_fact(self, key: str, category: str | None = None) -> str | None:
        with self.cursor() as cur:
            if category:
                cur.execute("SELECT value FROM facts WHERE key=? AND category=?", (key, category))
            else:
                cur.execute("SELECT value FROM facts WHERE key=? ORDER BY updated_at DESC LIMIT 1", (key,))
            row = cur.fetchone()
        return row["value"] if row else None

    def get_facts(self, category: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            if category:
                cur.execute("SELECT * FROM facts WHERE category=? ORDER BY key LIMIT ?", (category, limit))
            else:
                cur.execute("SELECT * FROM facts ORDER BY category, key LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def search_facts(self, needle: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{needle.lower()}%"
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM facts WHERE lower(key) LIKE ? OR lower(value) LIKE ? LIMIT ?",
                (like, like, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def delete_fact(self, key: str, category: str | None = None) -> bool:
        with self.cursor() as cur:
            if category:
                cur.execute("DELETE FROM facts WHERE key=? AND category=?", (key, category))
            else:
                cur.execute("DELETE FROM facts WHERE key=?", (key,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # short-term memory (conversation)
    # ------------------------------------------------------------------
    def add_message(self, session_id: str, role: str, content: str, *, tool_calls: Any = None,
                    meta: Any = None) -> int:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, meta, created_at) VALUES (?,?,?,?,?,?)",
                (session_id, role, content, json.dumps(tool_calls or []), json.dumps(meta or {}), utcnow()),
            )
            return int(cur.lastrowid or 0)

    def recent_messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (session_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            try:
                row["tool_calls"] = json.loads(row.get("tool_calls") or "[]")
            except json.JSONDecodeError:
                row["tool_calls"] = []
        return rows

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT session_id, COUNT(*) AS turns, MAX(created_at) AS last FROM messages "
                "GROUP BY session_id ORDER BY last DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def clear_session(self, session_id: str) -> int:
        with self.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            return cur.rowcount

    # ------------------------------------------------------------------
    # audit trail
    # ------------------------------------------------------------------
    def audit(self, *, tool: str, arguments: dict[str, Any] | None = None, permission: str = "safe",
              decision: str = "allow", reason: str = "", ok: bool = True, message: str = "",
              duration_ms: int = 0, simulated: bool = False, session_id: str = "", turn_id: str = "") -> int:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO audit (ts, session_id, turn_id, tool, arguments, permission, decision,
                                      reason, ok, message, duration_ms, simulated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (time.time(), session_id, turn_id, tool, json.dumps(arguments or {}, default=str),
                 permission, decision, reason, int(ok), message, int(duration_ms), int(simulated)),
            )
            return int(cur.lastrowid or 0)

    def audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            try:
                row["arguments"] = json.loads(row.get("arguments") or "{}")
            except json.JSONDecodeError:
                row["arguments"] = {}
        return rows

    def stats(self) -> dict[str, Any]:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) c FROM audit WHERE decision='block'")
            blocked = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) c FROM audit WHERE decision='confirm'")
            confirmed = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) c FROM audit")
            total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) c FROM reminders WHERE status='pending'")
            reminders = cur.fetchone()["c"]
        return {"tool_calls": total, "blocked": blocked, "confirmations": confirmed,
                "pending_reminders": reminders}

    # ------------------------------------------------------------------
    # artifacts
    # ------------------------------------------------------------------
    def add_artifact(self, kind: str, path: str, meta: dict[str, Any] | None = None) -> int:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO artifacts (kind, path, meta, created_at) VALUES (?,?,?,?)",
                (kind, path, json.dumps(meta or {}), utcnow()),
            )
            return int(cur.lastrowid or 0)

    def list_artifacts(self, kind: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            if kind:
                cur.execute("SELECT * FROM artifacts WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
            else:
                cur.execute("SELECT * FROM artifacts ORDER BY id DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            try:
                row["meta"] = json.loads(row.get("meta") or "{}")
            except json.JSONDecodeError:
                row["meta"] = {}
            row["exists"] = Path(row["path"]).exists()
        return rows

    # ------------------------------------------------------------------
    # pending confirmations
    # ------------------------------------------------------------------
    def save_pending(self, token: str, session_id: str, tool: str, arguments: dict[str, Any], prompt: str,
                     expires_in: float, plan_id: str | None = None, plan_index: int = 0) -> None:
        now = time.time()
        with self.cursor() as cur:
            cur.execute(
                """INSERT OR REPLACE INTO pending (token, session_id, tool, arguments, prompt, plan_id,
                                                   plan_index, status, created_at, expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (token, session_id, tool, json.dumps(arguments or {}, default=str), prompt, plan_id,
                 plan_index, "waiting", now, now + expires_in),
            )

    def get_pending(self, token: str) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM pending WHERE token=?", (token,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["arguments"] = json.loads(data.get("arguments") or "{}")
        except json.JSONDecodeError:
            data["arguments"] = {}
        data["expired"] = bool(data["expires_at"] and data["expires_at"] < time.time())
        return data

    def resolve_pending(self, token: str, status: str) -> bool:
        with self.cursor() as cur:
            cur.execute("UPDATE pending SET status=?, resolved_at=? WHERE token=?", (status, time.time(), token))
            return cur.rowcount > 0

    def list_pending(self, session_id: str | None = None) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            if session_id:
                cur.execute("SELECT * FROM pending WHERE session_id=? AND status='waiting' ORDER BY created_at",
                            (session_id,))
            else:
                cur.execute("SELECT * FROM pending WHERE status='waiting' ORDER BY created_at")
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            try:
                row["arguments"] = json.loads(row.get("arguments") or "{}")
            except json.JSONDecodeError:
                row["arguments"] = {}
        return rows

    # ------------------------------------------------------------------
    # plans
    # ------------------------------------------------------------------
    def save_plan(self, plan: dict[str, Any], session_id: str = "") -> None:
        stamp = utcnow()
        with self.cursor() as cur:
            cur.execute(
                """INSERT OR REPLACE INTO plans (id, session_id, title, goal, status, spec, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (plan["id"], session_id, plan.get("title", ""), plan.get("goal", ""), plan.get("status", "pending"),
                 json.dumps(plan, default=str), stamp, stamp),
            )

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM plans WHERE id=?", (plan_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["spec"] = json.loads(data["spec"])
        except (json.JSONDecodeError, KeyError):
            data["spec"] = {}
        return data

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute("SELECT id, title, goal, status, created_at FROM plans ORDER BY created_at DESC LIMIT ?",
                        (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    def export_snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": utcnow(),
            "reminders": self.list_reminders(status=None, limit=500),
            "events": self.list_events(limit=500),
            "tasks": self.list_tasks(limit=500),
            "facts": self.get_facts(limit=500),
            "stats": self.stats(),
        }

    def wipe(self, tables: Iterable[str] = ()) -> dict[str, int]:
        targets = list(tables) or ["reminders", "events", "tasks", "facts", "messages", "audit",
                                   "artifacts", "pending", "plans"]
        deleted: dict[str, int] = {}
        with self.cursor() as cur:
            for table in targets:
                if not table.replace("_", "").isalnum():
                    continue
                cur.execute(f"SELECT COUNT(*) c FROM {table}")
                deleted[table] = int(cur.fetchone()["c"])
                cur.execute(f"DELETE FROM {table}")
        return deleted
