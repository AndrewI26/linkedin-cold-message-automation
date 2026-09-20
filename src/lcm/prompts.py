"""Prompt construction and job/lead matching.

PROMPT_VERSION is stored on every draft, so when you change the wording you can
still tell which prompt produced which results.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from . import config

PROMPT_VERSION = "v1"

NOTE_LIMIT = 280  # LinkedIn caps connection notes at 300; leave margin.

SYSTEM = """\
You write short cold outreach messages for a student looking for software \
engineering work. You are given the sender's resume, their positioning, and a \
batch of recipients with the recipient's company's real open roles.

For each recipient write two things:
  - "note": a LinkedIn connection note, at most {note_limit} characters.
  - "message": a follow-up message of 90-130 words.

Rules, in order of importance:
1. Every message must name something specific and true about THAT recipient —
   their actual title, their team, or a real open role listed for their company.
   If a message could be sent unchanged to a different person, it is wrong.
2. Then name one specific thing from the sender's resume that maps to it.
   Prefer concrete numbers over adjectives.
3. Close with a low-friction ask (a short reply, a pointer to the right person,
   a 15-minute call) — never a demand and never an attachment.
4. Invent nothing. If the open-roles list is empty, work from the recipient's
   title and company alone and do not imply you saw a posting.
5. Banned, because they read as automated: "I hope this finds you well",
   "I came across your profile", "reaching out", "passionate", "synergy",
   "rockstar", "I wanted to", "leverage" as a verb, and any exclamation mark.
6. No greeting line in "note" beyond "Hi <first name> —". Do not sign either
   field; the sender's name is already attached.

{tone}

Return ONLY a JSON array, one object per recipient, in the order given:
[{{"id": <the id you were given>, "note": "...", "message": "..."}}]
No prose, no markdown fence.\
"""


def load_profile(path: Path | None = None) -> dict[str, Any]:
    path = path or config.PROFILE_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"No profile at {path}. It is drafted from your resume — see README."
        )
    return yaml.safe_load(path.read_text()) or {}


def score_job(title: str, match: dict[str, list[str]]) -> int:
    """Rank one posting's relevance. Negative means never cite it."""
    low = title.lower()
    if any(word in low for word in match.get("exclude", [])):
        return -1
    score = 0
    if any(word in low for word in match.get("prefer", [])):
        score += 10
    if any(word in low for word in match.get("include", [])):
        score += 3
    return score


def best_jobs(
    conn: sqlite3.Connection, company: str, match: dict, limit: int = 4
) -> list[sqlite3.Row]:
    """The few most relevant open roles at a company, best first."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE company = ?", (company,)
    ).fetchall()
    scored = [(score_job(r["title"], match), r) for r in rows]
    keep = [(s, r) for s, r in scored if s > 0]
    keep.sort(key=lambda pair: (-pair[0], pair[1]["title"]))
    return [r for _, r in keep[:limit]]


def build_system(profile: dict) -> str:
    tone = (profile.get("tone") or "").strip()
    return SYSTEM.format(
        note_limit=NOTE_LIMIT,
        tone=f"Tone: {tone}" if tone else "",
    )


def build_user(profile: dict, resume_text: str, batch: list[dict]) -> str:
    """One prompt covering a batch of recipients."""
    sender = {
        "name": profile.get("name"),
        "positioning": profile.get("positioning"),
        "seeking": profile.get("seeking"),
        "strengths": profile.get("strengths", []),
        "links": {k: v for k, v in (profile.get("links") or {}).items() if v},
    }
    return (
        "## Sender\n"
        + json.dumps(sender, indent=2, ensure_ascii=False)
        + "\n\n## Sender's resume\n"
        + resume_text.strip()
        + "\n\n## Recipients\n"
        + json.dumps(batch, indent=2, ensure_ascii=False)
        + f"\n\nWrite one object per recipient, {len(batch)} in total, "
        "in the same order. Return only the JSON array."
    )


def recipient_payload(lead: sqlite3.Row, jobs: list[sqlite3.Row]) -> dict:
    return {
        "id": lead["id"],
        "name": lead["name"],
        "first_name": (lead["name"] or "").split()[0] if lead["name"] else None,
        "title": lead["headline"],
        "company": lead["company"],
        "location": lead["location"],
        "open_roles": [
            {"title": j["title"], "location": j["location"]} for j in jobs
        ],
    }
