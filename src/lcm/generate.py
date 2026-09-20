"""Draft messages by shelling out to the `claude` CLI.

Uses your existing Claude Code login rather than an API key, so drafting costs
no money beyond your normal subscription usage. Leads are batched because CLI
startup, not tokens, is the dominant cost on this path.

Output is validated, never trusted: the model is asked for strict JSON, and a
batch that comes back malformed or over the character limit is retried before
anything reaches the database.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field

from . import prompts
from .config import Config, Resume

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class GenerationError(Exception):
    pass


class AuthError(GenerationError):
    """The CLI is not signed in. Retrying will not help."""


@dataclass
class GenerateResult:
    considered: int = 0
    drafted: int = 0
    batches: int = 0
    retries: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        line = (
            f"{self.drafted}/{self.considered} drafted "
            f"in {self.batches} batch(es)"
        )
        if self.retries:
            line += f", {self.retries} retry/retries"
        if self.errors:
            line += f", {len(self.errors)} failed"
        return line


def _extract_json(text: str) -> list[dict]:
    """Pull the JSON array out of a model response, fence or no fence."""
    candidate = text.strip()
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start, end = candidate.find("["), candidate.rfind("]")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]

    parsed = json.loads(candidate)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array")
    return parsed


def call_claude(system: str, user: str, model: str, timeout: int = 300) -> str:
    if not shutil.which("claude"):
        raise GenerationError(
            "The `claude` CLI is not on PATH. Install Claude Code, or switch the "
            "generator to an API key."
        )
    command = [
        "claude",
        "-p",
        user,
        "--append-system-prompt",
        system,
        "--model",
        model,
        "--output-format",
        "text",
        # A pure completion: no file access, no MCP servers, nothing to approve.
        "--allowed-tools",
        "",
        "--strict-mcp-config",
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    combined = f"{completed.stdout}\n{completed.stderr}"

    # The CLI reports auth failures on stdout and sometimes still exits 0, so
    # check the text rather than the exit code. This is the single most likely
    # failure, and it has a 30-second fix worth naming.
    if re.search(r"(OAuth session expired|Failed to authenticate|Invalid API key)", combined):
        raise AuthError(
            "The `claude` CLI is not authenticated.\n"
            "Run `claude` in a terminal, sign in with /login, then retry."
        )

    if completed.returncode != 0:
        detail = (completed.stderr.strip() or completed.stdout.strip())[:400]
        raise GenerationError(
            f"claude exited {completed.returncode}: {detail or '(no output)'}"
        )
    if not completed.stdout.strip():
        raise GenerationError("claude returned no output")
    return completed.stdout


def validate(items: list[dict], batch: list[dict]) -> dict[int, dict]:
    """Map lead id -> draft, rejecting anything malformed or overlong."""
    wanted = {row["id"] for row in batch}
    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            lead_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if lead_id not in wanted:
            continue
        note = (item.get("note") or "").strip()
        message = (item.get("message") or "").strip()
        if not note or not message:
            continue
        if len(note) > prompts.NOTE_LIMIT:
            continue
        out[lead_id] = {"note": note, "message": message}
    return out


def _run_batch(
    batch: list[dict], system: str, user_fn, model: str, attempts: int = 2
) -> tuple[dict[int, dict], int, str | None]:
    """Draft one batch, retrying once on malformed or incomplete output."""
    retries = 0
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            raw = call_claude(system, user_fn(batch, attempt), model)
            drafts = validate(_extract_json(raw), batch)
            if len(drafts) == len(batch):
                return drafts, retries, None
            last_error = f"got {len(drafts)} valid of {len(batch)}"
            # A partial batch still beats nothing on the final attempt.
            if attempt == attempts - 1 and drafts:
                return drafts, retries, last_error
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"unparseable response: {exc}"
        except AuthError:
            raise
        except (GenerationError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            if isinstance(exc, GenerationError) and "not on PATH" in str(exc):
                raise
        retries += 1
    return {}, retries, last_error


def pending_leads(conn: sqlite3.Connection, regenerate: bool) -> list[sqlite3.Row]:
    """Leads that still need a draft, oldest capture first."""
    clause = "" if regenerate else " WHERE d.id IS NULL"
    return conn.execute(
        "SELECT l.* FROM leads l"
        " LEFT JOIN drafts d ON d.lead_id = l.id"
        f"{clause}"
        " GROUP BY l.id ORDER BY l.captured_at, l.id"
    ).fetchall()


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    resume: Resume,
    resume_text: str,
    limit: int | None = None,
    regenerate: bool = False,
    model: str = "sonnet",
    dry_run: bool = False,
    on_batch=None,
) -> GenerateResult:
    profile = prompts.load_profile()
    match = profile.get("job_match", {}) or {}
    system = prompts.build_system(profile)

    leads = pending_leads(conn, regenerate)
    if limit:
        leads = leads[:limit]

    result = GenerateResult(considered=len(leads))
    if not leads:
        return result

    def user_fn(batch_payload, attempt):
        text = prompts.build_user(profile, resume_text, batch_payload)
        if attempt:
            text += (
                "\n\nThe previous attempt was rejected. Return ONLY a raw JSON "
                "array with one object per recipient, each with keys id, note "
                f"and message, and keep note under {prompts.NOTE_LIMIT} characters."
            )
        return text

    size = max(1, cfg.batch_size)
    for start in range(0, len(leads), size):
        chunk = leads[start : start + size]
        jobs_by_lead = {
            lead["id"]: prompts.best_jobs(conn, lead["company"] or "", match)
            for lead in chunk
        }
        batch = [
            prompts.recipient_payload(lead, jobs_by_lead[lead["id"]]) for lead in chunk
        ]
        result.batches += 1

        if dry_run:
            if on_batch:
                on_batch(system, user_fn(batch, 0), batch)
            continue

        drafts, retries, error = _run_batch(batch, system, user_fn, model)
        result.retries += retries
        if error and not drafts:
            result.errors.append(f"batch {result.batches}: {error}")
            continue

        for lead in chunk:
            draft = drafts.get(lead["id"])
            if not draft:
                continue
            jobs = jobs_by_lead[lead["id"]]
            conn.execute(
                "INSERT INTO drafts (lead_id, job_id, note, message, resume_name,"
                " prompt_version) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    lead["id"],
                    jobs[0]["id"] if jobs else None,
                    draft["note"],
                    draft["message"],
                    resume.name,
                    prompts.PROMPT_VERSION,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO outreach (lead_id, status) VALUES (?, 'pending')",
                (lead["id"],),
            )
            result.drafted += 1
        conn.commit()

    return result
