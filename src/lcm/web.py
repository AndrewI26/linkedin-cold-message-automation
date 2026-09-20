"""Local review queue.

Binds to 127.0.0.1 only: this database holds a few hundred real people's names
and has no business listening on the network.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import config, db, enrich, generate, ingest, prompts, resume as resume_mod

templates = Jinja2Templates(directory=str(config.PROJECT_ROOT / "templates"))

app = FastAPI(title="lcm review")

# Set by serve(); the CLI owns configuration, the app just reads it.
STATE: dict[str, Any] = {"pipeline_log": []}


def _conn() -> sqlite3.Connection:
    conn = db.connect()
    conn.executescript(db.SCHEMA)
    return conn


def _queue_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = db.counts_by_status(conn)
    cfg: config.Config = STATE["config"]
    return {
        "counts": counts,
        "leads": conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
        "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "sent_week": db.sent_this_week(conn),
        "budget": cfg.weekly_invite_budget,
    }


def _next_card(conn: sqlite3.Connection) -> dict | None:
    """The next lead awaiting a decision, with its draft and matched role."""
    row = conn.execute(
        """
        SELECT l.*, o.status AS status
        FROM leads l
        JOIN drafts d ON d.lead_id = l.id
        LEFT JOIN outreach o ON o.lead_id = l.id
        WHERE o.status IS NULL OR o.status = 'pending'
        GROUP BY l.id
        ORDER BY l.id
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None

    draft = db.latest_draft(conn, row["id"])
    job = None
    if draft and draft["job_id"]:
        job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (draft["job_id"],)
        ).fetchone()
    return {"lead": row, "draft": draft, "job": job}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = _conn()
    try:
        return templates.TemplateResponse(
            request,
            "review.html",
            {"card": _next_card(conn), "stats": _queue_counts(conn),
             "log": STATE["pipeline_log"][-5:]},
        )
    finally:
        conn.close()


@app.get("/card", response_class=HTMLResponse)
def card(request: Request):
    conn = _conn()
    try:
        return templates.TemplateResponse(
            request,
            "_card.html",
            {"card": _next_card(conn), "stats": _queue_counts(conn)},
        )
    finally:
        conn.close()


@app.get("/stats", response_class=HTMLResponse)
def stats(request: Request):
    conn = _conn()
    try:
        return templates.TemplateResponse(
            request, "_stats.html", {"stats": _queue_counts(conn)}
        )
    finally:
        conn.close()


@app.post("/outreach/{lead_id}", response_class=HTMLResponse)
def decide(
    request: Request,
    lead_id: int,
    status: Annotated[str, Form()],
    final_text: Annotated[str, Form()] = "",
):
    if status not in db.STATUSES:
        raise HTTPException(400, f"unknown status: {status}")

    conn = _conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "no such lead")

        db.set_status(
            conn, lead_id, status,
            final_text=final_text.strip() or None,
            channel="linkedin" if status in ("approved", "sent") else None,
        )
        conn.commit()
        return templates.TemplateResponse(
            request,
            "_card.html",
            {"card": _next_card(conn), "stats": _queue_counts(conn)},
        )
    finally:
        conn.close()


def _run_pipeline(step: str) -> None:
    """Run one pipeline stage and record a one-line outcome for the UI."""
    cfg: config.Config = STATE["config"]
    conn = _conn()
    try:
        conn.executescript(db.SCHEMA)
        if step == "ingest":
            outcome = str(ingest.run(conn))
        elif step == "enrich":
            outcome = str(enrich.run(conn))
        elif step == "generate":
            target = cfg.resolve_resume(STATE.get("resume_name"))
            text = resume_mod.extract(target)
            outcome = str(generate.run(conn, cfg, target, text))
        else:
            outcome = f"unknown step: {step}"
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI, not swallowed
        outcome = f"{step} failed: {exc}"
    finally:
        conn.close()
    STATE["pipeline_log"].append(f"{step}: {outcome}")


@app.post("/pipeline/{step}")
def pipeline(step: str, background: BackgroundTasks):
    if step not in ("ingest", "enrich", "generate"):
        raise HTTPException(404, "unknown step")
    background.add_task(_run_pipeline, step)
    return JSONResponse({"started": step})


@app.get("/log", response_class=HTMLResponse)
def log(request: Request):
    return templates.TemplateResponse(
        request, "_log.html", {"log": STATE["pipeline_log"][-5:]}
    )


def serve(cfg: config.Config, resume_name: str | None, host: str, port: int,
          open_browser: bool) -> None:
    import uvicorn

    STATE["config"] = cfg
    STATE["resume_name"] = resume_name
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
