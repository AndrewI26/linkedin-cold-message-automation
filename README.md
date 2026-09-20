# linkedin-cold-message-automation

Semi-automated, human-reviewed cold outreach to tech recruiters and hiring managers.

You run the LinkedIn search and click a bookmarklet on each results page. From
there the tool structures the leads, matches each company against its real open
roles on public job boards, drafts a tailored note grounded in your resume, and
hands you a keyboard-driven review queue. You send the messages yourself.

**Nothing here logs into LinkedIn or sends anything on your behalf.** LinkedIn's
User Agreement prohibits scraping and automated messaging, and they detect it;
a restricted account is an expensive thing to lose mid-job-hunt. So the two
steps that touch LinkedIn stay under your hand, and everything repetitive in
between is automated.

## Setup

```bash
uv sync
uv run lcm init          # finds your resume directory, writes config.toml
uv run lcm extract       # PDF -> data/resumes/<name>.md
```

Then read `data/resumes/<name>.md` and fix anything the PDF extractor mangled,
and edit `data/profile.yaml` — what you're seeking, which strengths to lead
with, and the keywords used to pick which open role to cite. Both steer every
message, so they are worth ten minutes.

Drafting shells out to the `claude` CLI, using your Claude Code subscription
rather than an API key. If it reports an auth error, run `claude` in a terminal
and sign in with `/login`.

## Running

```bash
uv run lcm capture                    # install the bookmarklet, once
uv run lcm ingest                     # drain data/inbox/
uv run lcm enrich                     # match companies to open roles
uv run lcm generate --limit 5 --dry-run   # inspect the prompt
uv run lcm generate                   # draft for real
uv run lcm review                     # http://127.0.0.1:8000
```

After `lcm review` is up you can drive the whole loop from the browser — Ingest,
Enrich and Generate are buttons. The queue is keyboard-driven:

| key | action |
|-----|--------|
| `a` | copy the note, open their profile, mark sent |
| `w` | copy and queue for later |
| `s` | skip |
| `x` | reject |
| `e` | edit the note |

The header meters your pace against a weekly invite budget (default 80; free
LinkedIn accounts get roughly 100 and exceeding it is its own way to get
restricted). Set it under `[settings]` in `config.toml`.

### Capturing

Run a People search on LinkedIn — filter by title, e.g. *Technical Recruiter* or
*Engineering Manager*, plus location. Click the bookmarklet once per results
page; a panel shows the running total. Captures accumulate across pages in the
browser's local storage, so click **Export** only on the last page, then move the
downloaded JSON into `data/inbox/`.

The scraper matches on DOM *structure*, not CSS classes — LinkedIn ships
obfuscated, rotating class names, so any class-based selector would be dead
within weeks. If a capture ever comes back empty, that heuristic is what needs
repairing: see `capture/capture.js` and the fixture in `tests/fixtures/`.

## How it fits together

```
LinkedIn search --> bookmarklet --> data/inbox/*.json
                                          |
                                     lcm ingest   (dedupe on profile URL)
                                          v
  Greenhouse / Lever public APIs --> lcm enrich --> leads.db
                                          |
    resume.md + profile.yaml --> lcm generate  (claude -p, batched)
                                          v
                                     lcm review --> you send
```

Everything is idempotent and keyed on the normalized profile URL, so
re-capturing overlapping search pages never produces a duplicate and never
double-messages anyone.

## Layout

| path | what it is |
|---|---|
| `src/lcm/config.py` | config.toml, resume directory resolution |
| `src/lcm/resume.py` | PDF -> markdown, cached against the PDF's mtime |
| `capture/capture.js` | the bookmarklet; structural DOM matching |
| `src/lcm/ingest.py` | JSON -> leads, deduped |
| `src/lcm/enrich.py` | Greenhouse/Lever lookups, cached including misses |
| `src/lcm/prompts.py` | prompt construction and job/lead matching |
| `src/lcm/generate.py` | `claude -p` batching, JSON validation, retries |
| `src/lcm/web.py` | FastAPI review queue, bound to localhost |
| `tests/fixtures/` | a LinkedIn-shaped DOM fixture for the scraper |

## Resumes

`config.toml` points at a *directory*, and every PDF in it is available by its
filename stem — drop a new one in and it works with no config edit.

```bash
uv run lcm resumes                    # list them, with extraction status
uv run lcm generate --resume internal # substring match is enough
```
