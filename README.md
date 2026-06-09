# Science Agent

Tracks, scores, and reports new cancer early-detection research for SpotitEarly.
A daily **tri-model pipeline** (Claude + Gemini + GPT) fetches publications from
journals and PubMed, gates them for relevance, scores relevancy and credibility,
stores everything in Postgres, and feeds weekly email digests, the unified
Company Brief, and an MCP connector for claude.ai / Custom GPTs.

## Architecture

```
config/sources.yaml      Journal + PubMed source definitions
ingest/                  Fetchers (RSS, PubMed E-utilities w/ NCBI_API_KEY)
tri_model/               Gating, Claude + Gemini reviewers, GPT evaluator
storage/                 store.py factory -> pg_store (Postgres) | sqlite_store (local dev)
enrich/                  Canonical URL / DOI / PMID extraction, OA-PDF cascade
digest/                  Digest data access, Jinja2 rendering, Gmail/SendGrid senders
scripts/                 generate_weekly_digest.py, generate_company_brief.py,
                         prepare_week_pdfs.py
mcp_server/              MCP server (stdio + streamable HTTP) with must-reads tools
upload_app/              Manual PDF upload web app (founder flow)
run_tri_model_daily.py   Daily pipeline entrypoint
run.py                   Legacy classic pipeline (local/reference use)
```

Data flow: scrapers fetch -> dedupe -> store in `publications` -> tri-model
review writes scores to `publications` (source of truth) and `tri_model_events`
(audit) -> digest/brief/MCP read scored rows.

## Production (GitHub Actions -> SSH -> EC2)

Production runs on an EC2 box (`ai.spotitearly.com`) with a local-only
PostgreSQL database. Each workflow SSHes in, does `git pull --ff-only origin
main`, activates the box's venv, and runs the relevant entrypoint. Required
repo secrets include `EC2_SSH_KEY` (hex-encoded), the LLM keys
(`CLAUDE_API_KEY`, `GEMINI_API_KEY`, `SPOTITEARLY_LLM_API_KEY`), `NCBI_API_KEY`,
and Gmail credentials for the email sends.

| Workflow | Schedule (UTC) | Purpose |
|---|---|---|
| `tri-model-daily.yml` | Daily 15:45 | Full tri-model pipeline + backend ingestion + LinkedIn sync |
| `prepare-week-pdfs.yml` | Wed 18:00 | OA-PDF prefetch (Unpaywall -> Europe PMC -> Crossref -> bioRxiv) for the week's must-reads |
| `pdf-reminder.yml` | Thu 03:00 | Reminder email if PDF uploads are still pending |
| `company-brief.yml` | Thu 06:00 | Unified weekly Company Brief email (BI + Science + Grant + Regulatory) |
| `weekly-digest.yml` | Manual only | Standalone Science digest (folded into the Company Brief) |
| `db-backup.yml` | Daily 04:00 | `pg_dump` of the production DB, uploaded as a 30-day artifact |
| `test.yml` | Push to main / PRs | pytest suite on Python 3.11 |

Any failed run opens a GitHub issue titled `[workflow-failure] ...` in this
repo, in addition to the step summary.

## Email outputs

- **Company Brief** (`scripts/generate_company_brief.py`) — weekly Thursday
  email consolidating competitive, science, grant, and regulatory sections.
  Science articles come from this repo's DB in-process; the other sections are
  fetched from sibling services over the EC2 loopback.
- **Weekly Digest** (`scripts/generate_weekly_digest.py`) — standalone science
  digest with must-reads, scores, feedback links, and PDF attachments; now
  manual-only.
- **OA-PDF prep** (`scripts/prepare_week_pdfs.py`) — Wednesday job that fetches
  open-access PDFs for the top must-reads and emails the founder upload links
  for anything still missing (served by `upload_app/`).

## MCP connector

`mcp_server/` exposes `get_must_reads`, `search_publications`, and
`get_publication` over stdio (Custom GPT) and streamable HTTP (claude.ai
connector). See [mcp_server/README.md](mcp_server/README.md).

## Local development

Without `DATABASE_URL`, storage falls back to SQLite at `data/db/acitrack.db`.

```bash
pip install -r requirements.txt

# Small local tri-model run (requires LLM keys in the environment)
export CLAUDE_API_KEY=... GEMINI_API_KEY=... SPOTITEARLY_LLM_API_KEY=...
python run_tri_model_daily.py --max-papers 5 --lookback-hours 48 --enable-gating

# Render a digest without sending
python scripts/generate_weekly_digest.py --week last --demo

# Tests
python -m pytest tests/ -q
```

To target the production schema locally, set
`DATABASE_URL=postgresql://...` and run Alembic migrations (`alembic upgrade
head`).

## Further documentation

Point-in-time setup notes, implementation summaries, and historical fix logs
live in [docs/](docs/). The scoring rubric is at
[docs/RELEVANCY_RUBRIC_v2.md](docs/RELEVANCY_RUBRIC_v2.md) and the system
overview at [docs/science-agent-overview.md](docs/science-agent-overview.md).
