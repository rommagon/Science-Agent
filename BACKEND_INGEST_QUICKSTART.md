# Backend Ingestion Quick Start

This guide covers ingesting tri-model daily run outputs into the Render-hosted Postgres backend.

## Overview

The backend ingestion system uploads three types of data:
1. **Manifest** (`manifest.json`) - run metadata and counts
2. **Must-Reads** (`must_reads.json`) - top-ranked publications
3. **Tri-Model Events** (`tri_model_events.jsonl`) - detailed scoring events

## Prerequisites

### Required Environment Variables

```bash
# Backend API endpoint
export BACKEND_URL="https://acitracker-backend-tri.onrender.com"

# Authentication key (obtain from backend admin)
export BACKEND_API_KEY="your-api-key-here"
```

### Required Dependencies

The ingestion script uses `requests` which is already in `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Usage

### 1. Run Tri-Model Daily with Auto-Ingestion

The simplest approach is to enable backend ingestion when running the tri-model pipeline:

```bash
# Set required env vars
export BACKEND_URL="https://acitracker-backend-tri.onrender.com"
export BACKEND_API_KEY="your-api-key"
export TRI_MODEL_MINI_DAILY="true"
export CLAUDE_API_KEY="your-claude-key"
export GEMINI_API_KEY="your-gemini-key"
export SPOTITEARLY_LLM_API_KEY="your-gpt-key"

# Run with backend ingestion
python3 -u run_tri_model_daily.py \
  --run-date 2026-01-12 \
  --max-papers 50 \
  --ingest-backend

# Use --ingest-strict to fail if ingestion fails
python3 -u run_tri_model_daily.py \
  --run-date 2026-01-12 \
  --max-papers 50 \
  --ingest-backend \
  --ingest-strict
```

**Flags:**
- `--ingest-backend`: Enable backend ingestion after run completes
- `--backend-url`: Override BACKEND_URL env var
- `--backend-api-key`: Override BACKEND_API_KEY env var
- `--ingest-chunk-size`: Batch size for events (default: 100)
- `--ingest-strict`: Exit with non-zero code if ingestion fails

### 2. Ingest Existing Run Outputs

If you already have tri-model run outputs, you can ingest them separately:

```bash
# Ingest a specific run
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12

# Dry-run mode (validate payloads without POSTing)
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12 \
  --dry-run

# Override chunk size for large runs
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12 \
  --chunk-size 200
```

**Script Options:**
- `--outdir`: Path to run output directory (required)
- `--backend-url`: Backend URL (default: BACKEND_URL env var)
- `--backend-api-key`: API key (default: BACKEND_API_KEY env var)
- `--mode`: Run mode (default: "tri-model-daily")
- `--run-id`: Run identifier (default: from manifest.json)
- `--chunk-size`: Batch size for events (default: 100)
- `--timeout`: Request timeout in seconds (default: 60)
- `--retries`: Retry attempts with exponential backoff (default: 3)
- `--dry-run`: Validate without POSTing

### 3. Verify Ingestion

Check that data was ingested successfully:

```bash
# Get latest run
curl -H "X-API-Key: $BACKEND_API_KEY" \
  "$BACKEND_URL/runs/latest?mode=tri-model-daily"

# Get must-reads for mode
curl -H "X-API-Key: $BACKEND_API_KEY" \
  "$BACKEND_URL/must-reads?mode=tri-model-daily"

# Query disagreements
curl -H "X-API-Key: $BACKEND_API_KEY" \
  "$BACKEND_URL/disagreements?run_id=tri-model-daily-2026-01-12&agreement=low,moderate&min_delta=10"
```

## GitHub Actions Integration

The tri-model daily workflow automatically ingests to the backend when running on schedule or when the `ingest` input is enabled.

### Workflow File

`.github/workflows/tri-model-daily.yml`

### Required Secrets

Configure these in your GitHub repository settings:

- `BACKEND_URL`
- `BACKEND_API_KEY`
- `CLAUDE_API_KEY`
- `GEMINI_API_KEY`
- `SPOTITEARLY_LLM_API_KEY`

### Manual Trigger

Run the workflow manually from GitHub Actions:

1. Go to Actions → Tri-Model Daily Run
2. Click "Run workflow"
3. Set parameters:
   - `max_papers`: Number of papers to review (default: 50)
   - `lookback_hours`: Window size in hours (default: 48)
   - `ingest`: Enable backend ingestion (default: true)

## Troubleshooting

### 401 Unauthorized / 403 Forbidden

**Problem:** Bad or missing API key

**Solution:**
```bash
# Check that API key is set
echo $BACKEND_API_KEY

# Verify with backend admin that key is valid
```

### 422 Unprocessable Entity

**Problem:** Schema mismatch between client and backend

**Symptoms:**
```
✗ Manifest ingestion failed: Client error: {"detail":"Validation error..."}
```

**Solution:**
1. Check backend logs for exact validation error
2. Verify your data matches expected schema:
   - Manifest should have `run_id`, `mode`, `counts`, etc.
   - Must-reads wrapper needs `run_id`, `mode`, and `must_reads` object
   - Events wrapper needs `run_id`, `mode`, and `events` array
3. Update backend schema if needed (backend repo)

### 5xx Server Errors

**Problem:** Backend service error or timeout

**Solution:**
- The ingestion script automatically retries with exponential backoff (default: 3 retries)
- Check Render backend logs for errors
- Verify Render backend service is running
- For persistent issues, reduce `--chunk-size` to send smaller batches

### Connection Timeout

**Problem:** Network issues or slow backend

**Solution:**
```bash
# Increase timeout
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12 \
  --timeout 120

# Reduce chunk size for more frequent progress
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12 \
  --chunk-size 50
```

### Missing Files

**Problem:** Output directory doesn't contain required files

**Symptoms:**
```
ERROR - Required file not found: data/outputs/.../manifest.json
```

**Solution:**
- Verify the tri-model run completed successfully
- Check that output directory contains:
  - `manifest.json`
  - `must_reads.json`
  - `tri_model_events.jsonl`
- If files are missing, re-run the tri-model pipeline

## Backend API Reference

### POST /ingest/run

Upload run manifest.

**Payload:** Raw manifest.json content (no wrapper)

**Example:**
```json
{
  "run_id": "tri-model-daily-2026-01-12",
  "mode": "tri-model-daily",
  "generated_at": "2026-01-12T10:30:00",
  "window_start": "2026-01-10T00:00:00",
  "window_end": "2026-01-12T00:00:00",
  "counts": {
    "raw_fetched": 955,
    "usable": 50,
    "gpt_evaluations": 50
  },
  "reviewers_used": ["claude", "gemini"]
}
```

### POST /ingest/must-reads

Upload must-reads data.

**Payload:** Wrapped format

**Example:**
```json
{
  "run_id": "tri-model-daily-2026-01-12",
  "mode": "tri-model-daily",
  "must_reads": {
    "run_id": "tri-model-daily-2026-01-12",
    "generated_at": "2026-01-12T10:30:00",
    "must_reads_count": 5,
    "must_reads": [...]
  }
}
```

### POST /ingest/tri-model-events

Bulk upsert tri-model events.

**Payload:** Wrapped format with events array

**Example:**
```json
{
  "run_id": "tri-model-daily-2026-01-12",
  "mode": "tri-model-daily",
  "events": [
    {
      "run_id": "tri-model-daily-2026-01-12",
      "mode": "tri-model-daily",
      "publication_id": "abc123...",
      "title": "Example Paper",
      "claude_review": {...},
      "gemini_review": {...},
      "gpt_eval": {...}
    }
  ]
}
```

## Local Development

### Test Backend Locally

If running the backend locally for development:

```bash
# Point to local backend
export BACKEND_URL="http://localhost:8000"
export BACKEND_API_KEY="dev-key"

# Run ingestion
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12
```

### Validate Schema

Use dry-run mode to validate payloads without sending:

```bash
python scripts/ingest_to_backend.py \
  --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12 \
  --dry-run
```

This validates:
- All required files exist
- JSON/JSONL files are well-formed
- Payloads have correct structure

## Support

For issues:
1. Check logs: ingestion script logs all HTTP responses
2. Verify backend health: `curl $BACKEND_URL/health`
3. Review backend logs on Render dashboard
4. Contact backend admin if credentials are invalid
