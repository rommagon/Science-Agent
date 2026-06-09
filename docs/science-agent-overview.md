# Science Agent — System Overview

## What It Does

Science Agent is an automated scientific literature monitoring pipeline. It scrapes publications daily from RSS feeds and APIs (PubMed, bioRxiv, medRxiv, arXiv, Nature, Science, Cell, Lancet, NEJM, JAMA, etc.), deduplicates them, and runs a **tri-model AI review** (Claude + Gemini score relevancy independently, GPT synthesizes a final consensus score). Publications scoring 70+ are surfaced in a weekly email digest.

## Pipeline Flow

```
1. INGEST        → Scrape RSS/APIs for new publications
2. DEDUPLICATE   → SHA256(title|source|url) as primary key
3. STORE         → Upsert into publications table
4. ENRICH        → Extract DOI/PMID, resolve canonical URLs
5. TRI-MODEL     → Claude scores (0-100) + Gemini scores (0-100) → GPT synthesizes final score
6. DUAL-WRITE    → Write scores to publications (source of truth) + tri_model_events (audit)
7. DIGEST        → Weekly email with top-scored papers + honorable mentions
```

## Database

PostgreSQL in production, SQLite for local dev. Storage backend is auto-selected via `DATABASE_URL` env var.

### `publications` — Single Source of Truth

The central table containing all publication data, enrichment, and scores.

| Column Group | Columns | Description |
|---|---|---|
| **Identity** | `id` (SHA256 hash PK), `title`, `authors`, `source`, `venue` | Core metadata |
| **Content** | `raw_text`, `summary`, `published_date`, `url` | Full text/abstract + AI summary |
| **Links** | `canonical_url`, `doi`, `pmid`, `source_type` | Resolved links with fallback chain |
| **Tri-Model Scores** | `final_relevancy_score` (0-100), `claude_score`, `gemini_score` | AI consensus relevancy |
| **Score Detail** | `final_relevancy_reason`, `final_summary`, `agreement_level`, `confidence`, `disagreements` | Reasoning and reviewer agreement |
| **Credibility** | `credibility_score` (0-100), `credibility_reason`, `credibility_confidence`, `credibility_signals_json` | Trust assessment |
| **Enrichment** | `modality_tags`, `sample_size`, `study_type`, `key_metrics`, `sponsor_flag` | Structured metadata |
| **Audit** | `run_id`, `scoring_run_id`, `scoring_updated_at`, `created_at` | Traceability |

### `tri_model_events` — Scoring Audit Trail

Full audit log of every scoring decision. One row per (run_id, publication_id).

| Column Group | Columns | Description |
|---|---|---|
| **Reviews** | `claude_review_json`, `gemini_review_json`, `gpt_eval_json` | Raw LLM outputs (JSON) |
| **Scores** | `final_relevancy_score`, `credibility_score`, `agreement_level` | Consensus results |
| **Performance** | `claude_latency_ms`, `gemini_latency_ms`, `gpt_latency_ms` | Per-model timing |
| **Versioning** | `prompt_versions_json`, `model_names_json` | Which prompts/models produced the scores |

### `runs` — Pipeline Run Metadata

One row per pipeline execution.

| Column | Description |
|---|---|
| `run_id` | Unique run identifier |
| `mode` | `daily`, `weekly`, or `tri-model-daily` |
| `window_start` / `window_end` | Time range queried |
| `total_fetched`, `total_deduped`, `new_count` | Ingestion stats |

### Other Tables

| Table | Purpose |
|---|---|
| `run_papers` | Per-run publication status tracking (new/unchanged/updated) |
| `weekly_digest_sends` | Which papers were sent in each digest email |
| `weekly_digest_feedback` | User up/down votes on individual papers |
| `publication_embeddings` | Dense vectors for semantic search |
| `must_reads_rerank_cache` | Cached LLM reranking results |
| `relevancy_scoring_events` | Legacy per-model scoring audit (superseded by tri_model_events) |

## Key Query Patterns

### Get top papers for a date range

```sql
SELECT * FROM publications
WHERE published_date BETWEEN :start AND :end
  AND final_relevancy_score >= 70
ORDER BY final_relevancy_score DESC, published_date DESC
LIMIT :top_n
```

### Link resolution fallback chain

```
canonical_url → url → doi.org/{doi} → pubmed.ncbi.nlm.nih.gov/{pmid}/ → None
```

### Schema-tolerant access

All queries dynamically detect available columns before executing. This means:
- No hard column requirements
- Graceful handling of partial migrations
- Backward compatibility with older schema versions

The primary key column name can vary (`id`, `publication_id`, or `pub_id`) and is detected at runtime via `_get_publications_table_metadata()`.

## Key Files

| File | Role |
|---|---|
| `run_tri_model_daily.py` | Main pipeline orchestrator |
| `storage/pg_store.py` | PostgreSQL storage operations |
| `storage/sqlite_store.py` | SQLite storage operations |
| `storage/store.py` | Storage backend factory |
| `digest/data_access.py` | Digest query logic (date range, scoring, link building) |
| `enrich/canonical_url.py` | DOI/PMID extraction, URL normalization |
| `acitrack_types.py` | `Publication` dataclass definition |
| `ingest/fetch.py` | Source scrapers |

## GitHub Actions Workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `tri-model-daily.yml` | Daily 15:45 UTC | Full pipeline: ingest → score → export |
| `weekly-digest.yml` | Thursday 1am EST | Generate and email weekly digest |
