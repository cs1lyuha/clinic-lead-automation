"""Stocarea lead-urilor (SQLite): idempotență pe livrare, deduplicare pe contact, istoric de statusuri."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    lead_id INTEGER NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    message TEXT NOT NULL,
    source TEXT NOT NULL,
    utm_campaign TEXT,
    intent TEXT NOT NULL,
    urgent INTEGER NOT NULL,
    language TEXT NOT NULL,
    score INTEGER NOT NULL,
    priority TEXT NOT NULL,
    estimated_value INTEGER NOT NULL,
    suggested_reply TEXT NOT NULL,
    qualifier TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    crm_id TEXT,
    duplicate_of INTEGER REFERENCES leads(id),
    created_at TEXT NOT NULL,
    contacted_at TEXT
);
CREATE INDEX IF NOT EXISTS leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS leads_email ON leads(email);
CREATE TABLE IF NOT EXISTS lead_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    event TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    at TEXT NOT NULL
);
"""

STATUSES = ("new", "contacted", "booked", "attended", "no_show", "lost", "spam")
DEDUP_WINDOW = timedelta(days=30)


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def delivery_seen(conn: sqlite3.Connection, delivery_id: str) -> int | None:
    row = conn.execute("SELECT lead_id FROM deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
    return row["lead_id"] if row else None


def find_recent_duplicate(conn: sqlite3.Connection, phone: str | None, email: str | None,
                          now: datetime) -> sqlite3.Row | None:
    if not phone and not email:
        return None
    since = (now - DEDUP_WINDOW).isoformat()
    return conn.execute(
        "SELECT * FROM leads WHERE duplicate_of IS NULL AND created_at >= ? AND "
        "((? IS NOT NULL AND phone = ?) OR (? IS NOT NULL AND email = ?)) ORDER BY id LIMIT 1",
        (since, phone, phone, email, email)).fetchone()


def insert_lead(conn: sqlite3.Connection, delivery_id: str, row: dict[str, Any]) -> int:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with conn:
        cur = conn.execute(f"INSERT INTO leads ({cols}) VALUES ({marks})", tuple(row.values()))
        lead_id = cur.lastrowid
        conn.execute("INSERT INTO deliveries VALUES (?,?,?)", (delivery_id, lead_id, row["created_at"]))
        conn.execute("INSERT INTO lead_events (lead_id, event, data, at) VALUES (?,?,?,?)",
                     (lead_id, "created", json.dumps({"priority": row["priority"]}), row["created_at"]))
    return lead_id


def log_event(conn: sqlite3.Connection, lead_id: int, event: str, at: datetime, **data: Any) -> None:
    with conn:
        conn.execute("INSERT INTO lead_events (lead_id, event, data, at) VALUES (?,?,?,?)",
                     (lead_id, event, json.dumps(data, ensure_ascii=False), at.isoformat()))


def set_status(conn: sqlite3.Connection, lead_id: int, status: str, at: datetime) -> sqlite3.Row:
    if status not in STATUSES:
        raise ValueError(f"status necunoscut: {status}")
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if lead is None:
        raise KeyError(lead_id)
    with conn:
        conn.execute("UPDATE leads SET status = ?, contacted_at = COALESCE(contacted_at, ?) WHERE id = ?",
                     (status, at.isoformat() if status != "new" else None, lead_id))
        conn.execute("INSERT INTO lead_events (lead_id, event, data, at) VALUES (?,?,?,?)",
                     (lead_id, "status", json.dumps({"from": lead["status"], "to": status}), at.isoformat()))
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
