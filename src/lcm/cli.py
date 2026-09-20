"""Typer entrypoint. Every pipeline step is scriptable and idempotent."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from . import (
    bookmarklet,
    config,
    db,
    enrich as enrich_mod,
    export as export_mod,
    generate as generate_mod,
    ingest as ingest_mod,
    resume as resume_mod,
    web as web_mod,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Semi-automated, human-reviewed cold outreach.",
)


def _die(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _load() -> config.Config:
    try:
        return config.load()
    except config.ConfigError as exc:
        _die(str(exc))
        raise  # unreachable; satisfies the type checker


@app.command()
def init(
    resume_dir: str = typer.Option(
        None, "--resume-dir", help="Directory holding your resume PDFs."
    ),
    default: str = typer.Option(
        None, "--default", help="Which resume to use when --resume is omitted."
    ),
) -> None:
    """Create config.toml and the database."""
    config.ensure_dirs()

    if resume_dir:
        directory = Path(resume_dir).expanduser()
        if not directory.is_dir():
            _die(f"Not a directory: {directory}")
    else:
        found = config.discover_resume_dir()
        if not found:
            _die(
                "Could not find a resume directory. Pass --resume-dir explicitly."
            )
        directory = found
        typer.echo(f"Found resumes in {directory}")

    cfg = config.Config(resume_dir=directory, default_resume=default)
    available = cfg.resumes()
    if not available:
        _die(f"No PDFs in {directory}")

    if not default:
        if len(available) == 1:
            default = available[0].name
        else:
            typer.echo("\nWhich resume should be the default?")
            for i, r in enumerate(available, 1):
                typer.echo(f"  {i}. {r.name}")
            choice = typer.prompt("Number", type=int, default=1)
            if not 1 <= choice <= len(available):
                _die("Out of range.")
            default = available[choice - 1].name
        cfg = config.Config(resume_dir=directory, default_resume=default)

    config.write(cfg)
    db.init()
    typer.secho(f"\nWrote {config.CONFIG_PATH}", fg=typer.colors.GREEN)
    typer.echo(f"Default resume: {default}")
    typer.echo(f"Database: {config.DB_PATH}")
    typer.echo("\nNext: lcm extract")


@app.command()
def resumes() -> None:
    """List available resumes and whether their extracted text is current."""
    cfg = _load()
    try:
        available = cfg.resumes()
    except config.ConfigError as exc:
        _die(str(exc))
        return
    if not available:
        _die(f"No PDFs in {cfg.resume_dir}")

    typer.echo(f"{cfg.resume_dir}\n")
    for r in available:
        marker = "*" if r.name == cfg.default_resume else " "
        state = "not extracted" if not r.cache_path.exists() else (
            "stale" if resume_mod.is_stale(r) else "current"
        )
        typer.echo(f" {marker} {r.name:<28} {state}")
    if cfg.default_resume:
        typer.echo("\n* = default")


@app.command()
def extract(
    which: str = typer.Option(None, "--resume", "-r", help="Resume name or path."),
    force: bool = typer.Option(False, "--force", help="Re-extract even if current."),
) -> None:
    """Extract a resume PDF to markdown for review."""
    cfg = _load()
    try:
        target = cfg.resolve_resume(which)
    except config.ConfigError as exc:
        _die(str(exc))
        return

    text = resume_mod.extract(target, force=force)
    typer.secho(f"Wrote {target.cache_path}", fg=typer.colors.GREEN)
    typer.echo(f"{len(text.splitlines())} lines, {len(text)} chars")
    typer.echo(
        "\nRead it and fix anything the PDF extractor mangled — this text is the"
        "\nfoundation for every message."
    )


@app.command()
def capture(
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the install page in your browser."
    ),
) -> None:
    """Build the capture bookmarklet and show the install instructions."""
    page = bookmarklet.build()
    typer.secho(f"Built {page}", fg=typer.colors.GREEN)
    if open_browser:
        import webbrowser

        webbrowser.open(page.as_uri())
    else:
        typer.echo(f"Open: {page.as_uri()}")


@app.command()
def ingest(
    path: str = typer.Argument(
        None, help="A JSON file or directory. Defaults to data/inbox/."
    ),
) -> None:
    """Load captured leads into the database, deduped by profile URL."""
    _load()
    source = Path(path).expanduser() if path else None
    if source and not source.exists():
        _die(f"No such path: {source}")

    with db.session() as conn:
        result = ingest_mod.run(conn, source)

    if result.files == 0:
        typer.secho(
            f"No .json files in {source or config.INBOX_DIR}", fg=typer.colors.YELLOW
        )
        return
    typer.secho(str(result), fg=typer.colors.GREEN)


@app.command()
def enrich(
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-probe companies already looked up."
    ),
) -> None:
    """Match lead companies to their open roles on public job boards."""
    _load()
    with db.session() as conn:
        result = enrich_mod.run(conn, refresh=refresh)
    typer.secho(str(result), fg=typer.colors.GREEN)
    for error in result.errors:
        typer.secho(f"  {error}", fg=typer.colors.YELLOW, err=True)


@app.command()
def generate(
    which: str = typer.Option(None, "--resume", "-r", help="Resume name or path."),
    limit: int = typer.Option(None, "--limit", "-n", help="Only draft this many."),
    regenerate: bool = typer.Option(
        False, "--regenerate", help="Redraft leads that already have a draft."
    ),
    model: str = typer.Option("sonnet", "--model", help="Model passed to claude -p."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the prompt instead of calling the model."
    ),
) -> None:
    """Draft a note and a message for each lead that lacks one."""
    cfg = _load()
    try:
        target = cfg.resolve_resume(which)
    except config.ConfigError as exc:
        _die(str(exc))
        return

    if resume_mod.is_stale(target):
        typer.secho(
            f"Resume text for '{target.name}' is missing or stale — run `lcm extract`.",
            fg=typer.colors.YELLOW,
        )
    text = resume_mod.extract(target)

    def show(system: str, user: str, batch: list) -> None:
        typer.secho("--- system ---", fg=typer.colors.CYAN)
        typer.echo(system)
        typer.secho("--- user ---", fg=typer.colors.CYAN)
        typer.echo(user)

    try:
        with db.session() as conn:
            result = generate_mod.run(
                conn, cfg, target, text,
                limit=limit, regenerate=regenerate, model=model,
                dry_run=dry_run, on_batch=show if dry_run else None,
            )
    except FileNotFoundError as exc:
        _die(str(exc))
        return
    except generate_mod.GenerationError as exc:
        _die(str(exc))
        return

    if result.considered == 0:
        typer.secho("Nothing to draft.", fg=typer.colors.YELLOW)
        return
    typer.secho(str(result), fg=typer.colors.GREEN)
    for error in result.errors:
        typer.secho(f"  {error}", fg=typer.colors.YELLOW, err=True)


@app.command()
def review(
    which: str = typer.Option(None, "--resume", "-r", help="Resume for the Generate button."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="Port."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Serve the review queue."""
    cfg = _load()
    typer.secho(f"http://{host}:{port}", fg=typer.colors.GREEN)
    web_mod.serve(cfg, which, host, port, open_browser)


@app.command()
def export(
    out: str = typer.Option("leads.csv", "--out", "-o", help="Output CSV path."),
    status_filter: str = typer.Option(
        None, "--status", help=f"One of: {', '.join(db.STATUSES)}"
    ),
) -> None:
    """Export leads and their drafts to CSV."""
    _load()
    if status_filter and status_filter not in db.STATUSES:
        _die(f"Unknown status '{status_filter}'. One of: {', '.join(db.STATUSES)}")
    path = Path(out).expanduser()
    with db.session() as conn:
        count = export_mod.write_csv(conn, path, status_filter)
    typer.secho(f"Wrote {count} row(s) to {path}", fg=typer.colors.GREEN)


@app.command()
def status() -> None:
    """Show counts at each pipeline stage."""
    _load()
    with db.session() as conn:
        leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        drafted = conn.execute(
            "SELECT COUNT(DISTINCT lead_id) FROM drafts"
        ).fetchone()[0]
        counts = db.counts_by_status(conn)
        week = db.sent_this_week(conn)

    typer.echo(f"leads    {leads}")
    typer.echo(f"jobs     {jobs}")
    typer.echo(f"drafted  {drafted}")
    typer.echo("")
    for name in db.STATUSES:
        typer.echo(f"{name:<9}{counts[name]}")
    typer.echo(f"\nsent this week: {week}")


def main() -> None:
    try:
        app()
    except config.ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
