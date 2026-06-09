# Tri-Model Credibility Scoring & Updates

## Overview

This document describes recent enhancements to the tri-model daily pipeline:

1. **Credibility Scoring**: Re-introduced credibility scoring using the classic pipeline's LLM-based credibility system
2. **Claude Model Switch**: Changed from Sonnet 4.5 to Haiku 3.5 for cost savings
3. **Uncapped Paper Processing**: Removed the 50-paper cap, making the pipeline process all available papers by default

## 1. Credibility Scoring

### What It Does

Every publication processed through the tri-model pipeline now receives a credibility assessment in addition to relevancy scoring. Credibility evaluates the rigor and trustworthiness of the research evidence (NOT relevance to SpotItEarly).

### Implementation

- **Scorer**: `tri_model/credibility.py` - Adapts the existing `mcp_server/llm_credibility.py` system
- **Model**: Uses OpenAI (via `SPOTITEARLY_LLM_API_KEY`) - same as classic pipeline
- **When**: Computed for every "usable" publication after GPT evaluation completes
- **Non-blocking**: Failures don't stop the pipeline; credibility fields will be null if scoring fails

### Output Schema

Credibility results include:

```python
{
    "credibility_score": int (0-100) or None,
    "credibility_reason": str,  # 1-2 sentence explanation
    "credibility_confidence": str,  # "low" | "medium" | "high"
    "credibility_signals": {  # Metadata about the study
        "peer_reviewed": bool,
        "preprint": bool,
        "study_type": str,
        "human_cohort": bool,
        "external_validation": bool,
        "multicenter": bool,
        "review_or_meta": bool,
        "correction_or_erratum": bool,
        "citation_count": int or None,
        "citations_per_year": float or None,
        "referenced_by_count": int or None,
        "citation_data_available": bool
    },
    "scored_at": str (ISO timestamp),
    "scoring_version": str,  # e.g., "poc_v3"
    "scoring_model": str  # e.g., "gpt-4o-mini"
}
```

### Where Credibility Appears

1. **must_reads.json**: Top 5 papers include credibility_score, credibility_reason, credibility_confidence, credibility_signals
2. **tri_model_events.jsonl**: Every evaluated paper includes credibility fields
3. **SQLite tri_model_scoring_events table**: Stored with columns: credibility_score, credibility_reason, credibility_confidence, credibility_signals_json
4. **Backend ingestion**: All credibility fields are POSTed to backend endpoints

### Credibility Rubric (Summary)

- **Publication Type**: Peer-reviewed (50 base) vs Preprint (30 base, capped at 65 max)
- **Study Design Boosts**: Prospective studies (+25), external validation (+20), RCT (+20), large sample (+10), etc.
- **Rigor Penalties**: Small sample (-10), no validation (-10), industry conflicts (-5)
- **Citation Signals**: High citations/year (+5), old paper with no citations (-10), recent papers neutral

### Example CLI Usage

```bash
# Run tri-model daily with credibility scoring (uses SPOTITEARLY_LLM_API_KEY)
python3 run_tri_model_daily.py --lookback-hours 48

# Check must-reads output for credibility
cat data/outputs/tri-model-daily/<run-id>/must_reads.json | jq '.must_reads[] | {title, credibility_score, credibility_reason}'
```

## 2. Claude Model Switch to Haiku 3.5

### Change

- **Old**: `claude-sonnet-4-5-20250929`
- **New**: `claude-3-5-haiku-20241022`

### Rationale

Haiku 3.5 provides significant cost savings while maintaining good performance for the review task.

### Configuration

Default is now Haiku 3.5, but can be overridden:

```bash
# Use environment variable to override
export CLAUDE_MODEL="claude-sonnet-4-5-20250929"
python3 run_tri_model_daily.py

# Or set in GitHub Secrets (CLAUDE_MODEL)
```

**File**: `config/tri_model_config.py:85`

## 3. Uncapped Paper Processing

### Change

The tri-model daily pipeline now processes **all available papers** by default (uncapped).

### Previous Behavior

- GitHub Actions defaulted to `--max-papers 50`
- Limited pipeline to reviewing at most 50 papers per run

### New Behavior

- **Default**: No cap - processes all papers in the lookback window
- **Optional**: Use `--max-papers N` to limit if needed (for testing or cost control)

### GitHub Actions Workflow

```yaml
workflow_dispatch:
  inputs:
    max_papers:
      description: 'Maximum papers to review (leave empty for uncapped)'
      required: false
      default: ''  # Empty = uncapped
```

**Scheduled runs** (daily at 1:00 PM UTC): Run uncapped by default

**Manual runs**: Can specify max_papers if desired

### CLI Examples

```bash
# Uncapped (processes all papers)
python3 run_tri_model_daily.py --lookback-hours 48

# Capped at 20 papers (for testing)
python3 run_tri_model_daily.py --lookback-hours 48 --max-papers 20

# Capped at 100 papers (cost control)
python3 run_tri_model_daily.py --lookback-hours 48 --max-papers 100
```

## Files Changed

### New Files

- `tri_model/credibility.py` - Credibility scoring adapter for tri-model
- `tests/test_tri_model_credibility.py` - Credibility integration tests
- `TRI_MODEL_CREDIBILITY_README.md` - This documentation

### Modified Files

**Tri-Model Pipeline**:
- `run_tri_model_daily.py` - Added credibility scoring call, updated storage calls, credibility in must_reads
- `config/tri_model_config.py` - Changed Claude model to Haiku 3.5

**Storage Layer**:
- `storage/sqlite_store.py` - Added credibility columns to tri_model_scoring_events table, updated store/export functions

**CI/CD**:
- `.github/workflows/tri-model-daily.yml` - Removed 50-paper default cap, made uncapped the default

## Environment Variables

### Required for Credibility

- `SPOTITEARLY_LLM_API_KEY` - OpenAI API key for credibility scoring (same as classic pipeline)

### Optional

- `CLAUDE_MODEL` - Override Claude model (default: claude-3-5-haiku-20241022)
- `GEMINI_MODEL` - Override Gemini model (default: gemini-2.0-flash-exp)
- `SPOTITEARLY_CRED_MODEL` - Override credibility model (default: gpt-4o-mini)

## GitHub Secrets

Ensure these secrets are configured in GitHub Actions:

- `CLAUDE_API_KEY` - Anthropic API key
- `GEMINI_API_KEY` - Google Gemini API key
- `SPOTITEARLY_LLM_API_KEY` - OpenAI API key (for GPT evaluation AND credibility)
- `BACKEND_URL` - Backend API URL
- `BACKEND_API_KEY` - Backend authentication key

## Testing

### Run Credibility Tests

```bash
python3 tests/test_tri_model_credibility.py
```

### Run Full Unicode Tests

```bash
python3 tests/test_tri_model_unicode_and_storage.py
```

### Test Local Run (with API keys)

```bash
export CLAUDE_API_KEY="..."
export GEMINI_API_KEY="..."
export SPOTITEARLY_LLM_API_KEY="..."

python3 run_tri_model_daily.py --lookback-hours 6 --max-papers 5
```

## Backend Ingestion

Credibility fields are automatically included in backend ingestion payloads:

### `/ingest/must-reads` Payload

```json
{
  "run_id": "tri-model-daily-2026-01-24",
  "mode": "tri-model-daily",
  "must_reads": {
    "must_reads": [
      {
        "id": "...",
        "title": "...",
        "final_relevancy_score": 85,
        "credibility_score": 72,
        "credibility_reason": "Peer-reviewed prospective study with external validation.",
        "credibility_confidence": "high",
        "credibility_signals": { ... }
      }
    ]
  }
}
```

### `/ingest/tri-model-events` Payload

```json
{
  "run_id": "tri-model-daily-2026-01-24",
  "mode": "tri-model-daily",
  "events": [
    {
      "publication_id": "...",
      "final_relevancy_score": 85,
      "credibility_score": 72,
      "credibility_reason": "...",
      "credibility_confidence": "high",
      "credibility_signals": { ... }
    }
  ]
}
```

## Migration Notes

### Existing Databases

The SQLite schema migration runs automatically on first use:

- Adds `credibility_score INTEGER` column
- Adds `credibility_reason TEXT` column
- Adds `credibility_confidence TEXT` column
- Adds `credibility_signals_json TEXT` column

Existing rows will have NULL values for credibility fields until re-processed.

### Backwards Compatibility

- Credibility fields are optional - backend should handle null values gracefully
- Old tri_model_events.jsonl files won't have credibility fields
- Must-reads without credibility will show null/empty values

## Performance Considerations

### Cost Impact

1. **Claude Model Switch**: Haiku 3.5 is ~10x cheaper than Sonnet 4.5 per token
2. **Credibility Scoring**: Adds one OpenAI API call per paper (~$0.0001 per call with gpt-4o-mini)
3. **Uncapped Processing**: More papers = more API calls, but no artificial 50-paper limit

### Runtime Impact

- Credibility scoring adds ~1-2 seconds per paper (parallel with storage)
- Non-blocking: Failures don't stop the pipeline
- Uncapped runs may take longer but process all available data

### Recommended Settings

- **Production (scheduled)**: Uncapped, full credibility scoring
- **Testing**: `--max-papers 5` to limit API costs
- **Cost Control**: `--max-papers 100` or `--max-papers 200` as needed

## Troubleshooting

### Credibility Score is None

- Check `SPOTITEARLY_LLM_API_KEY` is set
- Check logs for "SPOTITEARLY_LLM_API_KEY not set" warnings
- Verify OpenAI API key is valid and has credits

### GitHub Actions Timeout

- Uncapped runs may hit 120-minute timeout if too many papers
- Consider adding `max_papers` input to manual dispatch
- Check for PubMed rate limiting (429 errors) causing retries

### Backend Ingestion Failures

- Credibility fields should be optional in backend schema
- Check backend logs for JSON validation errors
- Verify credibility_signals_json is properly formatted

## Summary

The tri-model pipeline now:

1. ✅ Scores credibility for every paper using the same system as classic pipeline
2. ✅ Uses Haiku 3.5 for Claude reviews (cost savings)
3. ✅ Processes all available papers by default (uncapped)
4. ✅ Includes credibility in must_reads, events, and backend ingestion
5. ✅ Maintains backwards compatibility with existing backend/storage

All changes are live and ready for the next scheduled GitHub Actions run.
