"""Configuration loading and resume resolution.

Resumes live in a single directory; each PDF is named by its filename stem.
Dropping a new PDF into that directory makes it available with no config edit.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.toml"
DATA_DIR = PROJECT_ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"
RESUME_CACHE_DIR = DATA_DIR / "resumes"
DB_PATH = DATA_DIR / "leads.db"
PROFILE_PATH = DATA_DIR / "profile.yaml"

# Searched by `lcm init` when no config exists yet.
DISCOVERY_DIRS = [
    Path.home() / "Personal" / "Resumes",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.cwd(),
]


class ConfigError(Exception):
    """Raised for user-fixable configuration problems."""


@dataclass(frozen=True)
class Resume:
    name: str  # filename stem, e.g. "external-full-stack"
    path: Path

    @property
    def cache_path(self) -> Path:
        return RESUME_CACHE_DIR / f"{self.name}.md"


@dataclass(frozen=True)
class Config:
    resume_dir: Path
    default_resume: str | None
    weekly_invite_budget: int = 80
    batch_size: int = 10

    def resumes(self) -> list[Resume]:
        """Every PDF directly inside the resume directory, sorted by name.

        Subdirectories are ignored, so a `Transcripts/` folder alongside the
        resumes costs nothing.
        """
        if not self.resume_dir.is_dir():
            raise ConfigError(
                f"Resume directory does not exist: {self.resume_dir}\n"
                f"Fix the [resumes] dir in {CONFIG_PATH}, or run `lcm init`."
            )
        found = sorted(
            (p for p in self.resume_dir.glob("*.pdf") if p.is_file()),
            key=lambda p: p.stem.lower(),
        )
        return [Resume(name=p.stem, path=p) for p in found]

    def resolve_resume(self, ref: str | None = None) -> Resume:
        """Resolve a --resume argument to a single Resume.

        Order: exact stem, then unique case-insensitive substring, then a
        literal filesystem path. An ambiguous substring is an error naming the
        candidates rather than an arbitrary pick.
        """
        available = self.resumes()

        if ref is None:
            if self.default_resume:
                ref = self.default_resume
            elif len(available) == 1:
                return available[0]
            else:
                raise ConfigError(
                    "No resume specified and no default set. Pass --resume, or set\n"
                    f"[resumes] default in {CONFIG_PATH}.\n"
                    + _format_available(available)
                )

        for r in available:
            if r.name == ref:
                return r

        needle = ref.lower()
        matches = [r for r in available if needle in r.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            raise ConfigError(f"'{ref}' is ambiguous — matches: {names}")

        # Fall back to a literal path, so a one-off PDF outside the directory
        # still works without being moved.
        literal = Path(ref).expanduser()
        if literal.is_file() and literal.suffix.lower() == ".pdf":
            return Resume(name=literal.stem, path=literal.resolve())

        raise ConfigError(f"No resume matching '{ref}'.\n" + _format_available(available))


def _format_available(resumes: list[Resume]) -> str:
    if not resumes:
        return "No PDFs found in the resume directory."
    return "Available:\n" + "\n".join(f"  {r.name}" for r in resumes)


def load(required: bool = True) -> Config:
    """Read config.toml. With required=False, fall back to a discovered dir."""
    if not CONFIG_PATH.exists():
        if required:
            raise ConfigError(
                f"No config found at {CONFIG_PATH}. Run `lcm init` first."
            )
        return Config(resume_dir=discover_resume_dir() or Path.cwd(), default_resume=None)

    with CONFIG_PATH.open("rb") as fh:
        raw = tomllib.load(fh)

    resumes = raw.get("resumes", {})
    dir_value = resumes.get("dir")
    if not dir_value:
        raise ConfigError(f"[resumes] dir is missing from {CONFIG_PATH}.")

    settings = raw.get("settings", {})
    return Config(
        resume_dir=Path(dir_value).expanduser(),
        default_resume=resumes.get("default"),
        weekly_invite_budget=int(settings.get("weekly_invite_budget", 80)),
        batch_size=int(settings.get("batch_size", 10)),
    )


def discover_resume_dir() -> Path | None:
    """Best guess at where the resumes live, for `lcm init`."""
    for candidate in DISCOVERY_DIRS:
        if not candidate.is_dir():
            continue
        pdfs = [p for p in candidate.glob("*.pdf") if p.is_file()]
        if not pdfs:
            continue
        # A directory whose name says "resume" is a confident hit; otherwise
        # require a filename to say so, to avoid claiming all of ~/Downloads.
        if "resume" in candidate.name.lower() or "cv" in candidate.name.lower():
            return candidate
        if any(
            "resume" in p.stem.lower() or p.stem.lower() in ("cv",) for p in pdfs
        ):
            return candidate
    return None


def write(config: Config) -> None:
    CONFIG_PATH.write_text(
        "# Written by `lcm init`. Edit freely.\n"
        "[resumes]\n"
        f'dir     = "{config.resume_dir}"\n'
        + (f'default = "{config.default_resume}"\n' if config.default_resume else "")
        + "\n[settings]\n"
        f"weekly_invite_budget = {config.weekly_invite_budget}\n"
        f"batch_size = {config.batch_size}\n"
    )


def ensure_dirs() -> None:
    for d in (DATA_DIR, INBOX_DIR, RESUME_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
