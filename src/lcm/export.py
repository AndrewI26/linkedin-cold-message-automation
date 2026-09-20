"""CSV export, for working the list somewhere other than the review UI."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

COLUMNS = [
    "name", "headline", "company", "location", "profile_url",
    "status", "cited_role", "note", "message", "sent_at",
]


def rows(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    clause, params = "", ()
    if status:
        clause = " WHERE COALESCE(o.status, 'pending') = ?"
        params = (status,)

    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT l.name, l.headline, l.company, l.location, l.profile_url,
                   COALESCE(o.status, 'pending') AS status,
                   j.title AS cited_role,
                   d.note, d.message, o.sent_at
            FROM leads l
            LEFT JOIN outreach o ON o.lead_id = l.id
            LEFT JOIN drafts d ON d.id = (
                SELECT id FROM drafts WHERE lead_id = l.id
                ORDER BY created_at DESC, id DESC LIMIT 1
            )
            LEFT JOIN jobs j ON j.id = d.job_id
            """
            + clause
            + " ORDER BY l.company, l.name",
            params,
        )
    ]


def write_csv(conn: sqlite3.Connection, path: Path, status: str | None = None) -> int:
    data = rows(conn, status)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(data)
    return len(data)
