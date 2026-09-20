"""PDF resume extraction, cached against the source file's mtime.

Extraction is a starting point, not a finished artifact: PDF text extraction
mangles bullets and column order, and this text is the foundation for every
message. Read `data/resumes/<name>.md` once and correct it by hand. The cache
keys on mtime, so your corrections survive until you change the PDF itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Resume

CACHE_HEADER = "<!-- lcm:extracted-from={path} mtime={mtime} -->"
_HEADER_RE = re.compile(r"<!--\s*lcm:extracted-from=(?P<path>.*?) mtime=(?P<mtime>[\d.]+)\s*-->")

# Lines that are pure layout noise in most resume templates.
_NOISE_RE = re.compile(r"^\s*(page \d+( of \d+)?|\d+)\s*$", re.IGNORECASE)

# LaTeX icon fonts (FontAwesome et al.) have no Unicode mapping, so pdfplumber
# emits them as "(cid:131)". They carry no information worth keeping.
_CID_RE = re.compile(r"\(cid:\d+\)")


def is_stale(resume: Resume) -> bool:
    """True when the cached markdown is missing or older than the PDF."""
    cache = resume.cache_path
    if not cache.exists():
        return True
    match = _HEADER_RE.search(cache.read_text(errors="replace")[:500])
    if not match:
        return True
    return float(match.group("mtime")) != resume.path.stat().st_mtime


def extract(resume: Resume, force: bool = False) -> str:
    """Return the resume as markdown, extracting only when the cache is stale."""
    if not force and not is_stale(resume):
        return read_cached(resume)

    text = _extract_pdf(resume.path)
    resume.cache_path.parent.mkdir(parents=True, exist_ok=True)
    header = CACHE_HEADER.format(path=resume.path, mtime=resume.path.stat().st_mtime)
    resume.cache_path.write_text(f"{header}\n\n{text}\n")
    return text


def read_cached(resume: Resume) -> str:
    """The cached markdown with the provenance header stripped."""
    raw = resume.cache_path.read_text(errors="replace")
    return _HEADER_RE.sub("", raw, count=1).strip()


def _extract_pdf(path: Path) -> str:
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # layout=True preserves horizontal position, which keeps two-column
            # resume templates from interleaving into nonsense.
            raw = page.extract_text(layout=True, x_density=4.0) or ""
            pages.append(raw)
    return _tidy("\n".join(pages))


def _tidy(text: str) -> str:
    """Collapse extraction artifacts into something readable as markdown."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.replace("•", "-").replace("\xa0", " ")
        line = _CID_RE.sub(" ", line)
        # layout=True pads with spaces to preserve x-position; that padding has
        # done its job once the columns are untangled, so collapse it.
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _NOISE_RE.match(line):
            continue
        lines.append(line)
    while lines and lines[0] == "":
        lines.pop(0)
    return "\n".join(lines).strip()
