"""Typer entrypoint. Every pipeline step is scriptable and idempotent."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from . import bookmarklet, config, db, resume as resume_mod

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
