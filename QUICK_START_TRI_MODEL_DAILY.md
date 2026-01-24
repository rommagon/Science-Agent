# Quick Start: Tri-Model Daily Run

## Overview

The tri-model daily runner (`run_tri_model_daily.py`) uses the **EXACT SAME scraper/ingestion pipeline** as the classic daily run, but instead of using only GPT for relevancy scoring, it uses:

1. **Claude** (Anthropic) as first reviewer
2. **Gemini** (Google) as second reviewer
3. **GPT** (OpenAI) as meta-evaluator that synthesizes both reviews

This experimental system is **completely isolated** from the classic daily/weekly pipeline.

---

## Key Design Principles

### 1. Uses Classic Scraper Path

```
run_tri_model_daily.py
└─► fetch_publications(sources, since_date, run_id, outdir)  ← SAME as run.py
    └─► RSS + PubMed + Preprints
        └─► deduplicate_publications()  ← SAME dedupe logic
            └─► store_publications()  ← SAME database
```

**Result:** Candidate sets are comparable to classic daily runs for the same date/window.

### 2. Complete Output Isolation

**Run ID Format:**
- Classic daily: `daily-2026-01-12`
- Tri-model daily: `tri-model-daily-2026-01-12`

**Output Paths:**
- Classic: `data/outputs/daily/daily-YYYY-MM-DD/`
- Tri-model: `data/outputs/tri-model-daily/tri-model-daily-YYYY-MM-DD/`

**Drive Folders:**
- Classic: `Daily/`
- Tri-model: `TriModelDaily/`

### 3. Complete Audit Trail

**Database Table:** `tri_model_scoring_events`
- Stores every reviewer output (Claude + Gemini)
- Stores every evaluator output (GPT)
- Tracks latencies, prompt versions, model names
- Idempotent upsert keyed by (run_id, publication_id)

**JSONL Artifact:** `tri_model_events.jsonl`
- One line per paper
- Full review JSON + evaluation JSON
- Can be re-imported or analyzed separately

---

## Setup

### Required Environment Variables

```bash
# Feature flag (must be true)
export TRI_MODEL_MINI_DAILY=true

# At least ONE reviewer API key
export CLAUDE_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."

# Evaluator API key (required)
export SPOTITEARLY_LLM_API_KEY="sk-..."  # or OPENAI_API_KEY

# Optional: Model overrides
export CLAUDE_MODEL="claude-sonnet-4-5-20250929"  # default
export GEMINI_MODEL="gemini-2.0-flash-exp"  # default
```

### Install Dependencies

```bash
pip install anthropic google-generativeai
```

---

## Usage

### Basic Run (Today's Data)

```bash
python run_tri_model_daily.py
```

Default behavior:
- Uses today's date
- 48-hour lookback window (matches classic daily)
- Reviews all papers with abstracts
- Outputs to `data/outputs/tri-model-daily/tri-model-daily-YYYY-MM-DD/`

### Run for Specific Date

To replicate a previous daily run:

```bash
python run_tri_model_daily.py --run-date 2026-01-12
```

**Note:** This uses midnight-anchored window (00:00 to 00:00), which may differ from classic daily's actual run timestamp.

### Match Exact Window from Classic Daily (Recommended for Comparison)

To use the **EXACT SAME window** as a classic daily run for apples-to-apples comparison:

```bash
python run_tri_model_daily.py --match-daily-run daily-2026-01-12
```

This:
- Loads `data/manifests/daily/daily-2026-01-12.json`
- Uses the exact `window_start` and `window_end` timestamps
- Ensures candidate counts are comparable (fetched/deduplicated should match)
- Outputs to `tri-model-daily-2026-01-12_match-daily-2026-01-12/`

**Example comparison:**
```bash
# Classic daily (already ran)
jq '.counts' data/manifests/daily/daily-2026-01-12.json

# Tri-model with matched window
python run_tri_model_daily.py --match-daily-run daily-2026-01-12 --max-papers 50
jq '.counts' data/manifests/tri-model-daily/tri-model-daily-2026-01-12_match-daily-2026-01-12.json
```

**Expected result:** `fetched` and `deduplicated` counts should be identical or very close.

### Explicit Window Timestamps

For custom windows, use explicit timestamps:

```bash
python run_tri_model_daily.py \
  --window-start "2026-01-10T21:31:13" \
  --window-end "2026-01-12T21:31:13"
```

### Custom Lookback Window

```bash
python run_tri_model_daily.py --lookback-hours 72
```

### Limit Number of Papers

```bash
python run_tri_model_daily.py --max-papers 50
```

Useful for testing or cost control. Sorts by date (most recent first) and takes top N.

### Upload to Drive

```bash
python run_tri_model_daily.py --upload-drive
```

Requires:
- `ACITRACK_DRIVE_FOLDER_ID` environment variable
- `GOOGLE_APPLICATION_CREDENTIALS` environment variable

### Full Example

```bash
python run_tri_model_daily.py \
  --run-date 2026-01-12 \
  --lookback-hours 48 \
  --max-papers 100 \
  --upload-drive
```

---

## Output Files

### Output Directory Structure

```
data/outputs/tri-model-daily/tri-model-daily-2026-01-12/
├── tri_model_events.jsonl    # Complete audit trail (one line per paper)
├── must_reads.json            # Top 5 papers by final_relevancy_score
├── report.md                  # Human-readable markdown summary
└── manifest.json              # Run metadata (counts, window, etc.)

data/manifests/tri-model-daily/
└── tri-model-daily-2026-01-12.json  # Copy of manifest for easy discovery
```

### tri_model_events.jsonl

One JSON object per line:

```json
{
  "run_id": "tri-model-daily-2026-01-12",
  "mode": "tri-model-daily",
  "publication_id": "pub_abc123...",
  "title": "Early Detection of Lung Cancer Using Breath VOCs",
  "source": "Nature Cancer",
  "published_date": "2026-01-11",
  "claude_review": {
    "relevancy_score": 87,
    "relevancy_reason": "Highly relevant...",
    "signals": {"breath_based": true, "cancer_type": "lung", ...},
    "summary": "...",
    "concerns": "None",
    "confidence": "high"
  },
  "gemini_review": {
    "relevancy_score": 83,
    ...
  },
  "gpt_eval": {
    "final_relevancy_score": 85,
    "final_relevancy_reason": "Both reviewers agreed strongly...",
    "final_signals": {...},
    "final_summary": "...",
    "agreement_level": "high",
    "disagreements": "None",
    "evaluator_rationale": "Averaged scores due to high agreement",
    "confidence": "high"
  },
  "prompt_versions": {"claude": "v1", "gemini": "v1", "gpt": "v1"},
  "model_names": {"claude": "claude-sonnet-4-5-20250929", "gemini": "gemini-2.0-flash-exp", "gpt": "gpt-4o-mini"},
  "claude_latency_ms": 1234,
  "gemini_latency_ms": 987,
  "gpt_latency_ms": 1567,
  "created_at": "2026-01-12T14:23:45Z"
}
```

### must_reads.json

```json
{
  "run_id": "tri-model-daily-2026-01-12",
  "generated_at": "2026-01-12T14:30:00Z",
  "total_candidates": 150,
  "must_reads_count": 5,
  "must_reads": [
    {
      "id": "pub_abc123...",
      "title": "...",
      "source": "Nature Cancer",
      "published_date": "2026-01-11",
      "final_relevancy_score": 85,
      "final_relevancy_reason": "...",
      "final_summary": "...",
      "agreement_level": "high",
      "confidence": "high",
      "claude_score": 87,
      "gemini_score": 83
    }
  ]
}
```

### manifest.json

```json
{
  "run_id": "tri-model-daily-2026-01-12",
  "run_type": "tri-model-daily",
  "mode": "tri-model-daily",
  "generated_at": "2026-01-12T14:30:00Z",
  "window_start": "2026-01-10T00:00:00",
  "window_end": "2026-01-12T00:00:00",
  "window_mode": "midnight_anchored",
  "counts": {
    "raw_fetched": 1000,
    "window_filtered": 200,
    "deduplicated": 180,
    "usable": 150,
    "missing_abstract": 30,
    "reviewer_failures": 10,
    "gpt_evaluations": 140
  },
  "reviewers_used": ["claude", "gemini"],
  "local_output_paths": {
    "tri_model_events": "data/outputs/tri-model-daily/.../tri_model_events.jsonl",
    "must_reads": "...",
    "report": "...",
    "manifest": "..."
  }
}
```

---

## Window Modes and Comparison

### Understanding Window Modes

Tri-model daily supports three window determination modes:

1. **`matched_daily`** (via `--match-daily-run`):
   - Uses exact window from classic daily manifest
   - **Recommended for apples-to-apples comparison**
   - Example: Classic daily ran at 21:31:13, tri-model uses same timestamps
   - Output: `tri-model-daily-YYYY-MM-DD_match-daily-YYYY-MM-DD/`

2. **`explicit`** (via `--window-start` and `--window-end`):
   - Uses explicitly provided timestamps
   - Useful for custom windows or replaying specific time ranges
   - Output: `tri-model-daily-YYYY-MM-DD_explicit/`

3. **`midnight_anchored`** (default, via `--run-date`):
   - Uses midnight-to-midnight window (00:00 to 00:00)
   - **Not comparable to classic daily** due to different window boundaries
   - Classic daily uses actual run timestamp (e.g., 21:31:13)
   - Output: `tri-model-daily-YYYY-MM-DD/`

### Why Window Matching Matters

**The Problem:**

Classic scraper's `fetch_publications()` only accepts a `since_date` parameter (no upper bound). Internally, it fetches publications from `since_date` to **NOW**:
- PubMed uses `maxdate = datetime.now()` (see `ingest/fetch.py:476`)
- RSS feeds have no upper bound check

This means:
- Classic daily ran on Jan 12 → fetched Jan 10-12 (NOW was Jan 12)
- Tri-model ran on Jan 21 → fetched Jan 10-21 (NOW is Jan 21) ❌

**The Fix: Post-Fetch Window Filtering**

Tri-model applies **strict window filtering** after fetching to ensure historical parity:

1. Fetch publications from `window_start` (same as classic)
2. **Filter out** publications where `published_date > window_end`
3. Also filter out publications with missing/unparseable dates
4. Continue with deduplication and review

This ensures tri-model can replicate historical runs regardless of today's date.

**Example:**

```bash
python run_tri_model_daily.py --match-daily-run daily-2026-01-12 --max-papers 50
```

Result:
```
window_start: 2026-01-10T21:31:13+00:00  (from manifest)
window_end:   2026-01-12T21:31:13+00:00  (from manifest)

raw_fetched: 1075        (everything since Jan 10, including Jan 13-21)
window_filtered: 275     (filtered to Jan 10-12 window)
deduplicated: 267        (identical to classic daily!)
```

**Manifest Counts Explained:**

- `raw_fetched`: Publications returned by classic scraper (before window filter)
- `window_filtered`: Publications within `[window_start, window_end]` (after filter)
- `deduplicated`: Final count after deduplication (comparable to classic daily)

---

## Comparison with Classic Daily Run

### To Verify Candidate Set Parity

Run both for the same date:

```bash
# Classic daily (already ran)
ls data/outputs/daily/daily-2026-01-12/

# Tri-model daily (new)
python run_tri_model_daily.py --run-date 2026-01-12
ls data/outputs/tri-model-daily/tri-model-daily-2026-01-12/
```

Then compare counts:

```bash
# Classic daily counts
jq '.counts' data/manifests/daily/daily-2026-01-12.json

# Tri-model daily counts
jq '.counts' data/manifests/tri-model-daily/tri-model-daily-2026-01-12.json
```

Expected: `fetched` count should be identical (same scraper path).

---

## Agreement Analysis

### Check Agreement Levels

```bash
jq '[.must_reads[].agreement_level] | group_by(.) | map({level: .[0], count: length})' \
  data/outputs/tri-model-daily/tri-model-daily-2026-01-12/must_reads.json
```

Output:
```json
[
  {"level": "high", "count": 3},
  {"level": "moderate", "count": 2}
]
```

### Find Disagreements

```bash
jq '.must_reads[] | select(.agreement_level == "low") | {title, claude_score, gemini_score, final_score}' \
  data/outputs/tri-model-daily/tri-model-daily-2026-01-12/must_reads.json
```

---

## Cost Estimate

### Per-Paper Cost (Estimated)

- Claude API: ~$0.015/review
- Gemini API: ~$0.001/review (often free tier)
- GPT evaluator: ~$0.001/evaluation
- **Total: ~$0.017/paper**

### Full Daily Run (Estimated)

Typical daily run: ~200 papers with abstracts

- Claude: 200 × $0.015 = $3.00
- Gemini: 200 × $0.001 = $0.20
- GPT: 200 × $0.001 = $0.20
- **Total: ~$3.40 per daily run**

Compare to classic daily (GPT-only, ~$0.20).

**Tri-model is ~17× more expensive**, but provides multi-model validation.

---

## Troubleshooting

### "Tri-model system is not enabled"

Set environment variable:
```bash
export TRI_MODEL_MINI_DAILY=true
```

### "No reviewer API keys configured"

Set at least one:
```bash
export CLAUDE_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."
```

### "No OpenAI API key for GPT evaluator"

Set:
```bash
export SPOTITEARLY_LLM_API_KEY="sk-..."
```

### Claude API Rate Limit

Reduce `--max-papers` or add delays in `tri_model/reviewers.py`.

### Gemini API Errors

Check API key and quota. Gemini may have different rate limits than Claude/GPT.

### Empty Must-Reads

Check `manifest.json` for counts:
```bash
jq '.counts' data/outputs/tri-model-daily/tri-model-daily-YYYY-MM-DD/manifest.json
```

If `reviewer_failures` is high, check logs for API errors.

---

## Database Queries

### Get All Tri-Model Events for a Run

```sql
SELECT * FROM tri_model_scoring_events
WHERE run_id = 'tri-model-daily-2026-01-12'
ORDER BY final_relevancy_score DESC;
```

### Compare Scores Across Reviewers

```sql
SELECT
  title,
  json_extract(claude_review_json, '$.relevancy_score') AS claude_score,
  json_extract(gemini_review_json, '$.relevancy_score') AS gemini_score,
  final_relevancy_score,
  agreement_level
FROM tri_model_scoring_events
WHERE run_id = 'tri-model-daily-2026-01-12'
  AND claude_review_json IS NOT NULL
  AND gemini_review_json IS NOT NULL
ORDER BY final_relevancy_score DESC
LIMIT 20;
```

### Find High-Disagreement Papers

```sql
SELECT
  title,
  agreement_level,
  disagreements,
  final_relevancy_score
FROM tri_model_scoring_events
WHERE run_id = 'tri-model-daily-2026-01-12'
  AND agreement_level = 'low'
ORDER BY final_relevancy_score DESC;
```

---

## Next Steps

### After Running Once

1. **Analyze agreement patterns:**
   ```bash
   jq '.must_reads[] | {title, agreement: .agreement_level, claude: .claude_score, gemini: .gemini_score, final: .final_relevancy_score}' \
     data/outputs/tri-model-daily/tri-model-daily-YYYY-MM-DD/must_reads.json
   ```

2. **Review disagreements:**
   - Look at papers where Claude and Gemini scores differ by >20 points
   - Check GPT's evaluator_rationale for how it resolved disagreements

3. **Cost analysis:**
   - Check actual API costs from Anthropic, Google, OpenAI dashboards
   - Compare to classic daily run costs

4. **Quality assessment:**
   - Did multi-model catch edge cases single model missed?
   - Were disagreements productive (led to better final scores)?
   - Did evaluator make good synthesis decisions?

---

## Safety & Rollback

### Tri-Model is Completely Isolated

- **Classic daily/weekly pipeline:** Unchanged
- **Database:** Separate table (`tri_model_scoring_events`)
- **Outputs:** Separate directories
- **No risk of corrupting production data**

### To Disable

Simply stop running `run_tri_model_daily.py`. No cleanup needed.

### To Delete All Tri-Model Data

```bash
# Delete output directories
rm -rf data/outputs/tri-model-daily/
rm -rf data/manifests/tri-model-daily/

# Drop database table (optional)
sqlite3 data/db/acitrack.db "DROP TABLE IF EXISTS tri_model_scoring_events;"
```

---

## Full Documentation

- **Implementation Details:** `TRI_MODEL_SUMMARY.md`
- **Prompts:** `tri_model/prompts.py`
- **Architecture:** See "Tri-Model System" section in main README

---

**Ready to run! 🚀**

**Recommendation:** Start with a small test run:
```bash
python run_tri_model_daily.py --max-papers 10
```

Then scale up to full daily runs once validated.
