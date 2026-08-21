"""SQLite persistence for tickets, agent steps, decisions, and HITL queues."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from concierge.paths import db_path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db = path or db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    conn = conn or connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            received_at TEXT,
            from_name TEXT,
            from_email TEXT,
            subject TEXT,
            body TEXT,
            category TEXT,
            action TEXT,
            status TEXT,
            composite_confidence REAL,
            final_draft TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            output_json TEXT,
            confidence REAL,
            latency_ms INTEGER,
            error TEXT,
            created_at TEXT,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );

        CREATE TABLE IF NOT EXISTS decisions (
            ticket_id TEXT PRIMARY KEY,
            action TEXT,
            composite_confidence REAL,
            model_confidence REAL,
            heuristic_deltas_json TEXT,
            policy_flags_json TEXT,
            thresholds_json TEXT,
            rationale TEXT,
            created_at TEXT,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );

        CREATE TABLE IF NOT EXISTS queue_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            queue_type TEXT NOT NULL,
            payload_json TEXT,
            human_action TEXT,
            human_note TEXT,
            created_at TEXT,
            resolved_at TEXT,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );
        """
    )
    conn.commit()
    return conn


def ingest_ticket(conn: sqlite3.Connection, ticket: dict[str, Any]) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO tickets (
            id, received_at, from_name, from_email, subject, body,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ingested', ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            received_at=excluded.received_at,
            from_name=excluded.from_name,
            from_email=excluded.from_email,
            subject=excluded.subject,
            body=excluded.body,
            status='ingested',
            category=NULL,
            action=NULL,
            composite_confidence=NULL,
            final_draft=NULL,
            updated_at=excluded.updated_at
        """,
        (
            ticket["id"],
            ticket["received_at"],
            ticket["from_name"],
            ticket["from_email"],
            ticket["subject"],
            ticket["body"],
            now,
            now,
        ),
    )
    conn.execute("DELETE FROM agent_steps WHERE ticket_id = ?", (ticket["id"],))
    conn.execute("DELETE FROM decisions WHERE ticket_id = ?", (ticket["id"],))
    conn.execute("DELETE FROM queue_items WHERE ticket_id = ?", (ticket["id"],))
    conn.commit()


def record_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    agent_name: str,
    output: dict[str, Any] | None = None,
    confidence: float | None = None,
    latency_ms: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_steps (
            ticket_id, agent_name, output_json, confidence, latency_ms, error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticket_id,
            agent_name,
            json.dumps(output or {}, default=str),
            confidence,
            latency_ms,
            error,
            utc_now(),
        ),
    )


def record_decision(
    conn: sqlite3.Connection,
    ticket_id: str,
    action: str,
    composite_confidence: float,
    model_confidence: float,
    heuristic_deltas: dict[str, float],
    policy_flags: list[str],
    thresholds: dict[str, float],
    rationale: str,
) -> None:
    conn.execute(
        """
        INSERT INTO decisions (
            ticket_id, action, composite_confidence, model_confidence,
            heuristic_deltas_json, policy_flags_json, thresholds_json,
            rationale, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticket_id) DO UPDATE SET
            action=excluded.action,
            composite_confidence=excluded.composite_confidence,
            model_confidence=excluded.model_confidence,
            heuristic_deltas_json=excluded.heuristic_deltas_json,
            policy_flags_json=excluded.policy_flags_json,
            thresholds_json=excluded.thresholds_json,
            rationale=excluded.rationale,
            created_at=excluded.created_at
        """,
        (
            ticket_id,
            action,
            composite_confidence,
            model_confidence,
            json.dumps(heuristic_deltas),
            json.dumps(policy_flags),
            json.dumps(thresholds),
            rationale,
            utc_now(),
        ),
    )


def finalize_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    category: str | None,
    action: str,
    status: str,
    composite_confidence: float | None,
    final_draft: str | None,
) -> None:
    conn.execute(
        """
        UPDATE tickets
        SET category = ?, action = ?, status = ?, composite_confidence = ?,
            final_draft = ?, updated_at = ?
        WHERE id = ?
        """,
        (category, action, status, composite_confidence, final_draft, utc_now(), ticket_id),
    )
    conn.commit()


def enqueue(
    conn: sqlite3.Connection,
    ticket_id: str,
    queue_type: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO queue_items (ticket_id, queue_type, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (ticket_id, queue_type, json.dumps(payload, default=str), utc_now()),
    )
    conn.commit()


def list_queue(conn: sqlite3.Connection, open_only: bool = True) -> list[sqlite3.Row]:
    sql = """
        SELECT q.id, q.ticket_id, q.queue_type, q.payload_json, q.human_action,
               q.human_note, q.created_at, q.resolved_at,
               t.subject, t.from_name, t.status, t.action
        FROM queue_items q
        JOIN tickets t ON t.id = q.ticket_id
    """
    if open_only:
        sql += " WHERE q.human_action IS NULL"
    sql += " ORDER BY q.created_at ASC"
    return list(conn.execute(sql).fetchall())


def get_ticket_audit(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        return None
    steps = conn.execute(
        "SELECT * FROM agent_steps WHERE ticket_id = ? ORDER BY id ASC",
        (ticket_id,),
    ).fetchall()
    decision = conn.execute(
        "SELECT * FROM decisions WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    queues = conn.execute(
        "SELECT * FROM queue_items WHERE ticket_id = ? ORDER BY id ASC",
        (ticket_id,),
    ).fetchall()
    return {
        "ticket": dict(ticket),
        "steps": [dict(s) for s in steps],
        "decision": dict(decision) if decision else None,
        "queue_items": [dict(q) for q in queues],
    }


def apply_human_action(
    conn: sqlite3.Connection,
    ticket_id: str,
    human_action: str,
    note: str | None = None,
    edited_body: str | None = None,
) -> bool:
    row = conn.execute(
        """
        SELECT id FROM queue_items
        WHERE ticket_id = ? AND human_action IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (ticket_id,),
    ).fetchone()
    if row is None:
        return False

    now = utc_now()
    conn.execute(
        """
        UPDATE queue_items
        SET human_action = ?, human_note = ?, resolved_at = ?
        WHERE id = ?
        """,
        (human_action, note, now, row["id"]),
    )
    status = {"approved": "approved", "edited": "approved", "rejected": "rejected"}.get(
        human_action, human_action
    )
    if edited_body is not None:
        conn.execute(
            "UPDATE tickets SET final_draft = ?, status = ?, updated_at = ? WHERE id = ?",
            (edited_body, status, now, ticket_id),
        )
    else:
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, ticket_id),
        )
    conn.commit()
    return True
