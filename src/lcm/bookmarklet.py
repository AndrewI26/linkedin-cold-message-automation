"""Build the drag-to-bookmarks-bar install page from capture/capture.js.

No minifier: `javascript:` URLs survive percent-encoded newlines, so the
readable source with its comments intact can be embedded verbatim. That keeps
capture.js debuggable, which matters because LinkedIn's DOM will change.
"""

from __future__ import annotations

import html
import urllib.parse
from pathlib import Path

from .config import PROJECT_ROOT

CAPTURE_JS = PROJECT_ROOT / "capture" / "capture.js"
OUTPUT_HTML = PROJECT_ROOT / "capture" / "bookmarklet.html"

_PAGE = """<!DOCTYPE html>
<meta charset="utf-8">
<title>lcm capture — install</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 -apple-system, system-ui, sans-serif; max-width: 42rem;
         margin: 4rem auto; padding: 0 1.5rem; }}
  .bm {{ display: inline-block; background: #0a66c2; color: #fff;
        padding: .6rem 1.1rem; border-radius: 8px; text-decoration: none;
        font-weight: 600; cursor: grab; }}
  ol {{ padding-left: 1.2rem; }}
  li {{ margin: .6rem 0; }}
  code {{ background: rgba(127,127,127,.18); padding: .1rem .35rem;
         border-radius: 4px; font-size: .9em; }}
  .note {{ opacity: .75; font-size: .9rem; border-left: 3px solid #0a66c2;
          padding-left: 1rem; margin-top: 2.5rem; }}
</style>

<h1>lcm capture</h1>

<p>Drag this button to your bookmarks bar:</p>
<p><a class="bm" href="{href}">lcm capture</a></p>

<ol>
  <li>Show the bookmarks bar if it is hidden (<code>⌘⇧B</code> in Chrome and Safari).</li>
  <li>Drag the blue button up onto it.</li>
  <li>Run a People search on LinkedIn — filter by title, for example
      <em>Technical Recruiter</em> or <em>Engineering Manager</em>, plus location.</li>
  <li>Click the bookmarklet once per results page. A panel shows the running total.</li>
  <li>On the last page, click <strong>Export</strong>. A JSON file lands in
      <code>~/Downloads</code>.</li>
  <li>Move it into <code>{inbox}</code> and run <code>lcm ingest</code>.</li>
</ol>

<p class="note">
  This only reads the search results already rendered in your own browser —
  it never logs in, clicks through profiles, or sends anything. Captured people
  accumulate across pages in this site's local storage until you export.
</p>
"""


def build() -> Path:
    source = CAPTURE_JS.read_text()
    href = "javascript:" + urllib.parse.quote(source, safe="")
    page = _PAGE.format(
        href=html.escape(href, quote=True),
        inbox=html.escape(str(PROJECT_ROOT / "data" / "inbox")),
    )
    OUTPUT_HTML.write_text(page)
    return OUTPUT_HTML
