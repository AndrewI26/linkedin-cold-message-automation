"""Attach each lead's company to its real open roles.

Greenhouse and Lever publish unauthenticated JSON for every board they host.
This is what lets a message cite a specific requisition instead of saying
"I'm interested in opportunities at your company" — which is most of the
difference in whether anyone replies.

Slug guesses are cached, misses included, so a dead slug is probed once.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import httpx

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"

# Suffixes that are part of a legal name but never part of a board slug.
_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|sa|ag|"
    r"technologies|technology|labs|software|systems)\b\.?",
    re.IGNORECASE,
)

TIMEOUT = httpx.Timeout(10.0)


@dataclass
class EnrichResult:
    companies: int = 0
    hits: int = 0
    jobs_added: int = 0
    cached: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        line = (
            f"{self.companies} companies probed, {self.hits} with a public board, "
            f"{self.jobs_added} jobs stored ({self.cached} served from cache)"
        )
        if self.errors:
            line += f", {len(self.errors)} error(s)"
        return line


def slug_candidates(company: str) -> list[str]:
    """Plausible board slugs for a company name, most likely first."""
    base = _SUFFIXES.sub(" ", company)
    base = re.sub(r"[^\w\s-]", " ", base).strip().lower()
    words = base.split()
    if not words:
        return []

    joined = "".join(words)
    hyphenated = "-".join(words)
    candidates = [joined, hyphenated]
    if len(words) > 1:
        # "Shopify Commerce" is usually just "shopify".
        candidates.append(words[0])
    # Preserve order while dropping duplicates and anything implausibly short.
    seen: set[str] = set()
    return [c for c in candidates if len(c) > 1 and not (c in seen or seen.add(c))]


def _fetch(client: httpx.Client, source: str, slug: str) -> list[dict] | None:
    """Job dicts for a slug on one board, or None when the slug does not exist."""
    url = (GREENHOUSE if source == "greenhouse" else LEVER).format(slug=slug)
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None

    raw = payload.get("jobs", []) if source == "greenhouse" else payload
    if not isinstance(raw, list) or not raw:
        return None

    jobs = []
    for item in raw:
        if source == "greenhouse":
            location = (item.get("location") or {}).get("name")
            title, link = item.get("title"), item.get("absolute_url")
        else:
            location = (item.get("categories") or {}).get("location")
            title, link = item.get("text"), item.get("hostedUrl")
        if title:
            jobs.append({"title": title, "url": link, "location": location})
    return jobs or None


def _cached_lookup(conn: sqlite3.Connection, company: str, source: str):
    return conn.execute(
        "SELECT slug, hit FROM board_lookups WHERE company = ? AND source = ?",
        (company, source),
    ).fetchone()


def enrich_company(
    conn: sqlite3.Connection, client: httpx.Client, company: str, result: EnrichResult
) -> None:
    result.companies += 1
    for source in ("greenhouse", "lever"):
        cached = _cached_lookup(conn, company, source)
        if cached is not None:
            result.cached += 1
            if cached["hit"]:
                result.hits += 1
                # Already placed on this board; don't probe the other one.
                break
            continue

        found_slug, jobs = None, None
        for slug in slug_candidates(company):
            jobs = _fetch(client, source, slug)
            if jobs:
                found_slug = slug
                break

        conn.execute(
            "INSERT OR REPLACE INTO board_lookups (company, source, slug, hit)"
            " VALUES (?, ?, ?, ?)",
            (company, source, found_slug or "", 1 if jobs else 0),
        )
        if not jobs:
            continue

        result.hits += 1
        for job in jobs:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO jobs (company, title, url, location, source)"
                " VALUES (?, ?, ?, ?, ?)",
                (company, job["title"], job["url"], job["location"], source),
            )
            result.jobs_added += cursor.rowcount
        # One board per company is enough; no need to probe the other.
        break


def run(conn: sqlite3.Connection, refresh: bool = False) -> EnrichResult:
    result = EnrichResult()
    if refresh:
        conn.execute("DELETE FROM board_lookups")

    companies = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT company FROM leads WHERE company IS NOT NULL AND company != ''"
            " ORDER BY company"
        )
    ]
    if not companies:
        return result

    headers = {"User-Agent": "lcm/0.1 (personal job search tool)"}
    with httpx.Client(timeout=TIMEOUT, headers=headers, follow_redirects=True) as client:
        for company in companies:
            try:
                enrich_company(conn, client, company, result)
            except Exception as exc:  # noqa: BLE001 - one bad company must not abort the run
                result.errors.append(f"{company}: {exc}")
    return result
