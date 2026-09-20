"""Load captured JSON into the leads table.

Idempotent by `profile_url`: re-ingesting the same file, or overlapping
captures from two search pages, must never produce a second row for a person.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import config

_PROFILE_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)


@dataclass
class IngestResult:
    files: int = 0
    seen: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.files} file(s), {self.seen} record(s): "
            f"{self.added} new, {self.updated} updated, {self.skipped} skipped"
        )


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    match = _PROFILE_RE.search(raw)
    return f"https://www.linkedin.com/in/{match.group(1)}" if match else None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    return text or None


def normalize(record: dict) -> dict | None:
    """Shape one captured record, or None if it is unusable."""
    url = normalize_url(record.get("profile_url"))
    name = _clean(record.get("name"))
    if not url or not name:
        return None
    return {
        "name": name,
        "headline": _clean(record.get("headline")),
        "company": _clean(record.get("company")),
        "location": _clean(record.get("location")),
        "profile_url": url,
        "raw_json": json.dumps(record, ensure_ascii=False),
    }


def upsert(conn: sqlite3.Connection, lead: dict) -> str:
    """Insert or enrich one lead. Returns 'added', 'updated' or 'skipped'.

    An existing row is only ever filled in, never blanked: a later sparse
    capture of someone must not erase a headline an earlier one found.
    """
    existing = conn.execute(
        "SELECT * FROM leads WHERE profile_url = ?", (lead["profile_url"],)
    ).fetchone()

    if existing is None:
        conn.execute(
            "INSERT INTO leads (name, headline, company, location, profile_url, raw_json)"
            " VALUES (:name, :headline, :company, :location, :profile_url, :raw_json)",
            lead,
        )
        return "added"

    fills = {
        field: lead[field]
        for field in ("headline", "company", "location")
        if lead[field] and not existing[field]
    }
    if not fills:
        return "skipped"

    assignments = ", ".join(f"{field} = ?" for field in fills)
    conn.execute(
        f"UPDATE leads SET {assignments} WHERE id = ?",
        (*fills.values(), existing["id"]),
    )
    return "updated"


def ingest_file(conn: sqlite3.Connection, path: Path, result: IngestResult) -> None:
    payload = json.loads(path.read_text())
    records = payload if isinstance(payload, list) else list(payload.values())
    for record in records:
        result.seen += 1
        lead = normalize(record) if isinstance(record, dict) else None
        if lead is None:
            result.skipped += 1
            continue
        outcome = upsert(conn, lead)
        setattr(result, outcome, getattr(result, outcome) + 1)


def run(conn: sqlite3.Connection, source: Path | None = None) -> IngestResult:
    """Ingest one JSON file, or every JSON file in data/inbox/."""
    result = IngestResult()
    if source and source.is_file():
        paths = [source]
    else:
        directory = source or config.INBOX_DIR
        paths = sorted(directory.glob("*.json"))

    for path in paths:
        result.files += 1
        ingest_file(conn, path, result)
    return result
