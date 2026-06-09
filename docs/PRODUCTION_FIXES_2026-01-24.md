# Production Fixes - 2026-01-24

This document describes two critical production fixes implemented for the tri-model daily pipeline.

## Issue 1: Anthropic 404 Model Not Found

### Problem

The Claude model `claude-3-5-haiku-20241022` returns a 404 error in production:

```
Error: not_found_error for model "claude-3-5-haiku-20241022"
```

### Solution

Implemented automatic model fallback in the Claude reviewer layer:

1. **Model Fallback Chain**: If the preferred model (from `CLAUDE_MODEL` env var) returns a 404 `not_found_error`, the system automatically retries with fallback models in order:
   - `claude-3-haiku-20240307` (stable Haiku 3.0)
   - `claude-3-5-sonnet-20241022` (Sonnet 3.5 as last resort)

2. **Error Detection**: The system detects 404 errors by checking:
   - HTTP status code 404, OR
   - Error string contains "not_found_error" and "model"

3. **Logging**: The system logs which model was successfully used for each review

4. **GitHub Actions Stability**: Updated `.github/workflows/tri-model-daily.yml` to set `CLAUDE_MODEL=claude-3-haiku-20240307` for scheduled runs to ensure stability

### Files Changed

- `tri_model/reviewers.py`: Added model fallback logic in `claude_review()` function
- `.github/workflows/tri-model-daily.yml`: Set `CLAUDE_MODEL` environment variable

### Testing

```bash
# Test locally with different CLAUDE_MODEL values
export CLAUDE_API_KEY="your-key"
export CLAUDE_MODEL="claude-3-5-haiku-20241022"  # Will fallback if not found
python3 run_tri_model_daily.py --lookback-hours 6 --max-papers 2

# Test with stable model
export CLAUDE_MODEL="claude-3-haiku-20240307"
python3 run_tri_model_daily.py --lookback-hours 6 --max-papers 2
```

## Issue 2: SQLite Schema Mismatch

### Problem

Runtime error when storing tri-model events:

```
Error: table tri_model_scoring_events has no column named credibility_score
```

This occurs when the table was created at schema version 7 but without credibility columns (e.g., due to partial deployment or race condition).

### Solution

Made credibility column migration truly idempotent by:

1. **New Function**: Added `_ensure_tri_model_credibility_columns()` that runs on every database initialization
2. **Dynamic Column Check**: Uses `PRAGMA table_info()` to check actual table columns, not just schema version
3. **Safe Column Addition**: Only adds missing columns, skips if they already exist
4. **Always Runs**: Called after all version migrations complete to ensure consistency

### Files Changed

- `storage/sqlite_store.py`:
  - Moved credibility column check out of `_migrate_to_v7()`
  - Created new `_ensure_tri_model_credibility_columns()` function
  - Called from `_init_schema()` after all migrations

### Testing

Created comprehensive migration tests in `tests/test_sqlite_tri_model_migration.py`:

```bash
# Run migration tests
python3 tests/test_sqlite_tri_model_migration.py

# Tests verify:
# 1. Old schema (v6) without credibility columns migrates correctly
# 2. Columns are added via ALTER TABLE
# 3. Inserts work with credibility data
# 4. Migration is idempotent (can run multiple times safely)
```

## Quick Local Smoke Test

Run a minimal local test to verify both fixes:

```bash
# Set environment variables
export CLAUDE_API_KEY="your-anthropic-key"
export GEMINI_API_KEY="your-gemini-key"
export SPOTITEARLY_LLM_API_KEY="your-openai-key"
export CLAUDE_MODEL="claude-3-haiku-20240307"

# Run with 2 papers (fast test)
python3 run_tri_model_daily.py --lookback-hours 6 --max-papers 2

# Verify outputs
ls -lh data/outputs/tri-model-daily/
cat data/outputs/tri-model-daily/*/must_reads.json | jq '.must_reads[] | {title, credibility_score}'

# Check database
sqlite3 data/db/acitrack.db "PRAGMA table_info(tri_model_scoring_events)" | grep credibility
sqlite3 data/db/acitrack.db "SELECT publication_id, credibility_score FROM tri_model_scoring_events LIMIT 5"
```

Expected results:
- Claude review should succeed with model fallback logged
- must_reads.json should include credibility_score, credibility_reason, etc.
- Database should have credibility columns and data

## Test Results

All tests pass:

### Migration Tests
```bash
$ python3 tests/test_sqlite_tri_model_migration.py
Running tri-model migration tests...

✓ Created old schema without credibility columns
✓ Ran schema initialization/migration
✓ Verified credibility columns were added
✓ Successfully inserted event with credibility data
✓ Verified stored data is correct

✅ All migration tests passed!

✓ Schema initialization 1/3 completed
✓ Schema initialization 2/3 completed
✓ Schema initialization 3/3 completed
✓ Verified columns only added once (idempotent)

✅ Idempotency test passed!
```

### Credibility Tests
```bash
$ python3 tests/test_tri_model_credibility.py
✓ test_credibility_scorer_schema passed
✓ test_credibility_result_fields passed
✓ test_credibility_graceful_degradation passed

✅ All tri-model credibility tests passed!
```

## Deployment Checklist

- [x] Model fallback implemented and tested
- [x] GitHub Actions workflow updated with stable CLAUDE_MODEL
- [x] SQLite migration made idempotent
- [x] Migration tests created and passing
- [x] All existing tests still pass
- [ ] Commit and push to branch
- [ ] Test in staging (GitHub Actions manual dispatch)
- [ ] Merge to main
- [ ] Monitor scheduled run for successful execution

## Rollback Plan

If issues occur:

1. **Model Fallback Issues**: Set `CLAUDE_MODEL=claude-3-5-sonnet-20241022` in GitHub Actions to use Sonnet instead
2. **Database Issues**: The migration is safe and idempotent - it only adds columns if missing
3. **Complete Rollback**: Revert to commit before these changes and use previous stable model

## Related Documentation

- See `TRI_MODEL_CREDIBILITY_README.md` for complete credibility scoring documentation
- See `.github/workflows/tri-model-daily.yml` for GitHub Actions configuration
- See `storage/sqlite_store.py` for database schema details
