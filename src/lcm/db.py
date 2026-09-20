"""SQLite schema and helpers.

`profile_url` is the natural key for a person: you will re-capture overlapping
search pages, and the one unforgivable bug in this tool is messaging someone
twice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    headline     TEXT,
    company      TEXT,
    location     TEXT,
    profile_url  TEXT NOT NULL UNIQUE,
    source       TEXT NOT NULL DEFAULT 'linkedin-capture',
    captured_at  TEXT NOT NULL DEFAULT (datetime('now')),
    raw_json     TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT,
    location    TEXT,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, url)
);

-- Remembers which company slugs resolved on which board, including misses, so
-- enrichment does not re-probe the same dead slug on every run.
CREATE TABLE IF NOT EXISTS board_lookups (
    company     TEXT NOT NULL,
    source      TEXT NOT NULL,
    slug        TEXT NOT NULL,
    hit         INTEGER NOT NULL,
    checked_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (company, source)
);

CREATE TABLE IF NOT EXISTS drafts (
    id             INTEGER PRIMARY KEY,
    lead_id        INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    job_id         INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    note           TEXT NOT NULL,
    message        TEXT NOT NULL,
    resume_name    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS drafts_lead_idx ON drafts(lead_id);

CREATE TABLE IF NOT EXISTS outreach (
    id          INTEGER PRIMARY KEY,
    lead_id     INTEGER NOT NULL UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending',
    channel     TEXT,
    final_text  TEXT,
    sent_at     TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (status IN ('pending','approved','sent','skipped','rejected'))
);
"""

STATUSES = ("pending", "approved", "sent", "skipped", "rejected")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def latest_draft(conn: sqlite3.Connection, lead_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM drafts WHERE lead_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (lead_id,),
    ).fetchone()


def counts_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Every status, zero-filled, plus leads with no outreach row yet."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM outreach GROUP BY status"
    ).fetchall()
    counts = {s: 0 for s in STATUSES}
    for row in rows:
        counts[row["status"]] = row["n"]
    counts["pending"] += conn.execute(
        "SELECT COUNT(*) FROM leads l LEFT JOIN outreach o ON o.lead_id = l.id"
        " WHERE o.id IS NULL"
    ).fetchone()[0]
    return counts


def sent_this_week(conn: sqlite3.Connection) -> int:
    """Messages sent since the start of the current week.

    LinkedIn meters invites weekly; so does the review UI's budget display.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM outreach WHERE status = 'sent'"
        " AND sent_at >= date('now', 'weekday 0', '-7 days')"
    ).fetchone()[0]


def set_status(
    conn: sqlite3.Connection,
    lead_id: int,
    status: str,
    final_text: str | None = None,
    channel: str | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    sent_at = "datetime('now')" if status == "sent" else "NULL"
    conn.execute(
        f"""
        INSERT INTO outreach (lead_id, status, channel, final_text, sent_at, updated_at)
        VALUES (?, ?, ?, ?, {sent_at}, datetime('now'))
        ON CONFLICT(lead_id) DO UPDATE SET
            status     = excluded.status,
            channel    = COALESCE(excluded.channel, outreach.channel),
            final_text = COALESCE(excluded.final_text, outreach.final_text),
            sent_at    = COALESCE(excluded.sent_at, outreach.sent_at),
            updated_at = datetime('now')
        """,
        (lead_id, status, channel, final_text),
    )
