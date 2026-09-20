# linkedin-cold-message-automation

Semi-automated, human-reviewed cold outreach to tech recruiters and hiring managers.

You run the LinkedIn search and click a bookmarklet per results page; the tool
structures the leads, matches them against the company's real open roles from
public Greenhouse/Lever APIs, drafts a tailored note grounded in your resume,
and hands you a keyboard-driven review queue. You send the messages yourself.

Nothing here logs into LinkedIn or sends anything on your behalf — LinkedIn's
User Agreement prohibits scraping and automated messaging, and a restricted
account is an expensive thing to lose mid-job-hunt.

## Usage

```bash
uv sync
uv run lcm init
uv run lcm capture      # install the bookmarklet
uv run lcm ingest       # drain data/inbox/
uv run lcm enrich
uv run lcm generate --limit 5 --dry-run
uv run lcm review       # http://127.0.0.1:8000
```

Status: in development.
