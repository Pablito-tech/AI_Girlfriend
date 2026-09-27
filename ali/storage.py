"""Persistencia en SQLite: sesiones, mensajes y recuerdos.

Un solo archivo (`data/ali.db`) guarda todo lo que Ali vive. Como se escribe
en cada mensaje, aunque el programa se cierre de golpe no se pierde nada.
Las habilidades (agenda, multimedia...) crean sus propias tablas aquí mismo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .utils import iso, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    summary         TEXT NOT NULL DEFAULT '',
    reflected_upto  INTEGER NOT NULL DEFAULT 0   -- último id de mensaje ya consolidado
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    content        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    importance     INTEGER NOT NULL,
    tags           TEXT NOT NULL DEFAULT '',
    tokens         TEXT NOT NULL DEFAULT '',     -- texto ya tokenizado para búsquedas rápidas
    created_at     TEXT NOT NULL,
    last_accessed  TEXT NOT NULL,
    access_count   INTEGER NOT NULL DEFAULT 0,
    follow_up_at   TEXT,                         -- fecha para preguntar "¿cómo te fue?"
    followed_up    INTEGER NOT NULL DEFAULT 0,
    source         TEXT NOT NULL DEFAULT 'chat', -- chat | reflexion | manual
    session_id     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- atajos -----------------------------------------------------------

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def close(self) -> None:
        self.conn.close()

    # --- sesiones y mensajes ---------------------------------------------

    def start_session(self) -> int:
        return self.execute("INSERT INTO sessions (started_at) VALUES (?)", (iso(now()),)).lastrowid

    def end_session(self, session_id: int) -> None:
        self.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (iso(now()), session_id))

    def add_message(self, session_id: int, role: str, content: str) -> int:
        return self.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, iso(now())),
        ).lastrowid

    def recent_messages(self, limit: int, before_session: int | None = None) -> list[dict[str, Any]]:
        """Los últimos `limit` mensajes (en orden cronológico)."""
        if before_session is None:
            rows = self.query("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self.query(
                "SELECT * FROM messages WHERE session_id < ? ORDER BY id DESC LIMIT ?",
                (before_session, limit),
            )
        return [dict(r) for r in reversed(rows)]

    def unreflected_messages(self, session_id: int) -> list[dict[str, Any]]:
        row = self.query_one("SELECT reflected_upto FROM sessions WHERE id = ?", (session_id,))
        upto = row["reflected_upto"] if row else 0
        rows = self.query(
            "SELECT * FROM messages WHERE session_id = ? AND id > ? ORDER BY id", (session_id, upto)
        )
        return [dict(r) for r in rows]

    def sessions_pending_reflection(self, exclude: int | None = None) -> list[int]:
        rows = self.query(
            """
            SELECT s.id FROM sessions s
            WHERE s.id != COALESCE(?, -1)
              AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.id > s.reflected_upto)
            ORDER BY s.id
            """,
            (exclude,),
        )
        return [r["id"] for r in rows]

    def mark_reflected(self, session_id: int, upto_message_id: int, summary: str | None = None) -> None:
        if summary:
            self.execute(
                """UPDATE sessions SET reflected_upto = ?,
                   summary = CASE WHEN summary = '' THEN ? ELSE summary || ' ' || ? END
                   WHERE id = ?""",
                (upto_message_id, summary, summary, session_id),
            )
        else:
            self.execute("UPDATE sessions SET reflected_upto = ? WHERE id = ?", (upto_message_id, session_id))

    def last_session_summary(self, before_session: int) -> str:
        row = self.query_one(
            "SELECT summary FROM sessions WHERE id < ? AND summary != '' ORDER BY id DESC LIMIT 1",
            (before_session,),
        )
        return row["summary"] if row else ""
