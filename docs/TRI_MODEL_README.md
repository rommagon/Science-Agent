## Tri-Model Mini-Daily Experimental System

### Overview

This experimental feature implements a **tri-model review system** for mini-daily runs:

1. **Claude** (Anthropic) reviews each paper
2. **Gemini** (Google) reviews each paper
3. **GPT** (OpenAI) acts as meta-evaluator, analyzing both reviews and producing final decision

### Architecture

```
┌──────────────┐
│  Mini-Daily  │
│   Runner     │
└──────┬───────┘
       │
       ├─► Fetch Publications (6-hour window, max 10 papers)
       │
       ├─► Deduplicate
       │
       ├─► For each paper:
       │   │
       │   ├─► Claude Review
       │   │   ├─ relevancy_score (0-100)
       │   │   ├─ relevancy_reason
       │   │   ├─ signals (cancer_type, breath_based, etc.)
       │   │   ├─ summary
       │   │   ├─ concerns
       │   │   └─ confidence
       │   │
       │   ├─► Gemini Review
       │   │   └─ (same schema as Claude)
       │   │
       │   └─► GPT Evaluator
       │       ├─ Compares Claude + Gemini reviews
       │       ├─ final_relevancy_score
       │       ├─ final_relevancy_reason
       │       ├─ final_signals
       │       ├─ final_summary
       │       ├─ agreement_level (high/moderate/low)
       │       ├─ disagreements
       │       └─ evaluator_rationale
       │
       └─► Outputs:
           ├─ tri_model_reviews.json (raw reviews)
           ├─ tri_model_final.json (final decisions)
           ├─ must_reads.json (top 5 papers)
           ├─ report.md
           └─ manifest.json
```

### Key Design Decisions

#### 1. Separate Output Paths

Mini-daily outputs are completely isolated from standard daily/weekly runs:

```
data/outputs/mini-daily/mini-daily-YYYY-MM-DD/
  ├─ tri_model_reviews.json
  ├─ tri_model_final.json
  ├─ must_reads.json
  ├─ report.md
  └─ manifest.json

data/manifests/mini-daily/
  └─ mini-daily-YYYY-MM-DD.json

Drive/MiniDaily/mini-daily-YYYY-MM-DD/
  └─ (same files)

Drive/Manifests/MiniDaily/
  └─ mini-daily-YYYY-MM-DD.json
```

#### 2. Graceful Degradation

- If Claude API fails → GPT evaluates Gemini review only
- If Gemini API fails → GPT evaluates Claude review only
- If both fail for a paper → Paper is skipped (not fabricated)
- If GPT evaluator fails → No final decision (paper skipped)

#### 3. Shared Review Schema

All reviewers (Claude, Gemini) use identical JSON schema:

```json
{
  "relevancy_score": 0-100,
  "relevancy_reason": "string",
  "signals": {
    "cancer_type": "breast|lung|colon|other|none",
    "breath_based": true|false,
    "sensor_based": true|false,
    "animal_model": true|false,
    "ngs_genomics": true|false,
    "early_detection_focus": true|false
  },
  "summary": "string",
  "concerns": "string",
  "confidence": "low|medium|high"
}
```

GPT evaluator adds:
- `final_*` prefix for authoritative decisions
- `agreement_level`, `disagreements`, `evaluator_rationale`

#### 4. Prompt Versioning

All prompts are versioned and stored in `tri_model/prompts.py`:
- Claude: `CLAUDE_REVIEW_PROMPT_V1` (version v1)
- Gemini: `GEMINI_REVIEW_PROMPT_V1` (version v1)
- GPT: `GPT_EVALUATOR_PROMPT_V1` (version v1)

Versions are tracked in output metadata for reproducibility.

---

### Setup

#### Environment Variables

**Required:**
```bash
# Feature flag (must be true)
export TRI_MODEL_MINI_DAILY=true

# At least ONE reviewer API key
export CLAUDE_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."

# Evaluator API key (required)
export SPOTITEARLY_LLM_API_KEY="sk-..."  # or OPENAI_API_KEY
```

**Optional:**
```bash
# Model overrides
export CLAUDE_MODEL="claude-sonnet-4-5-20250929"  # default
export GEMINI_MODEL="gemini-2.0-flash-exp"  # default

# Mini-daily parameters
export MINI_DAILY_WINDOW_HOURS=6  # default
export MINI_DAILY_MAX_PAPERS=10  # default

# Drive upload
export ACITRACK_DRIVE_FOLDER_ID="..."
export GOOGLE_APPLICATION_CREDENTIALS="path/to/creds.json"
```

#### Dependencies

Install additional dependencies:

```bash
pip install anthropic google-generativeai
```

---

### Usage

#### Local Run

```bash
# Basic run (6-hour window, max 10 papers)
python run_mini_daily.py

# Custom parameters
python run_mini_daily.py \
  --lookback-hours 12 \
  --max-papers 20 \
  --upload-drive

# Full options
python run_mini_daily.py --help
```

#### GitHub Actions (Recommended for One-Off Tomorrow Run)

1. **Navigate to:** `Actions > Mini-Daily Tri-Model Run`

2. **Click:** "Run workflow"

3. **Configure:**
   - Lookback hours: `6` (default)
   - Max papers: `10` (default)
   - Upload to Drive: ✅ (checked)

4. **Click:** "Run workflow"

5. **Monitor:** Watch logs in real-time

6. **Artifacts:** Download outputs from workflow page

#### Scheduled Run (Alternative)

To run automatically tomorrow at a specific time, add to workflow:

```yaml
on:
  schedule:
    - cron: '0 14 * * *'  # 2 PM UTC daily
  workflow_dispatch:  # Keep manual trigger
```

**Note:** For one-time tomorrow run, `workflow_dispatch` is safer.

---

### Outputs

#### `tri_model_reviews.json`

Raw reviews from Claude and Gemini for each paper:

```json
{
  "run_id": "mini-daily-2026-01-21",
  "generated_at": "2026-01-21T14:00:00Z",
  "reviewers_used": ["claude", "gemini"],
  "total_reviewed": 10,
  "reviews": [
    {
      "publication_id": "pub_123",
      "title": "...",
      "source": "Nature Cancer",
      "claude_review": {
        "success": true,
        "review": { /* Claude's review schema */ },
        "model": "claude-sonnet-4-5-20250929",
        "version": "v1",
        "latency_ms": 1234
      },
      "gemini_review": {
        "success": true,
        "review": { /* Gemini's review schema */ },
        "model": "gemini-2.0-flash-exp",
        "version": "v1",
        "latency_ms": 987
      },
      "gpt_evaluation": {
        "success": true,
        "evaluation": { /* GPT's final decision */ },
        "model": "gpt-4o-mini",
        "version": "v1",
        "latency_ms": 1567,
        "inputs_used": {
          "claude_available": true,
          "gemini_available": true
        }
      }
    }
  ]
}
```

#### `tri_model_final.json`

Final decisions from GPT evaluator:

```json
{
  "run_id": "mini-daily-2026-01-21",
  "generated_at": "2026-01-21T14:00:00Z",
  "total_evaluated": 10,
  "final_decisions": [
    {
      "id": "pub_123",
      "title": "...",
      "final_relevancy_score": 85,
      "final_relevancy_reason": "...",
      "final_signals": { /* ... */ },
      "final_summary": "...",
      "agreement_level": "high",
      "disagreements": "None",
      "evaluator_rationale": "Both reviewers agreed strongly...",
      "confidence": "high",
      "claude_score": 87,
      "gemini_score": 83
    }
  ]
}
```

#### `must_reads.json`

Top 5 papers by final score:

```json
{
  "run_id": "mini-daily-2026-01-21",
  "generated_at": "2026-01-21T14:00:00Z",
  "window_hours": 6,
  "total_candidates": 10,
  "must_reads": [
    {
      "id": "pub_123",
      "title": "...",
      "final_relevancy_score": 85,
      /* ... same as final_decisions entry ... */
    }
  ]
}
```

#### `report.md`

Human-readable markdown summary with must-reads.

#### `manifest.json`

Run metadata with counts and file pointers:

```json
{
  "run_id": "mini-daily-2026-01-21",
  "run_type": "mini-daily",
  "counts": {
    "fetched": 15,
    "deduplicated": 12,
    "reviewed": 10,
    "must_reads": 5
  },
  "reviewers_used": ["claude", "gemini"],
  "local_output_paths": { /* ... */ },
  "drive_output_paths": { /* ... */ },
  "drive_file_ids": { /* ... */ }
}
```

---

### Comparison with Standard Daily/Weekly Runs

| Feature | Standard Daily/Weekly | Mini-Daily Tri-Model |
|---------|----------------------|---------------------|
| **Lookback Window** | 48 hours / 7 days | 6 hours (configurable) |
| **Papers Processed** | 200-300 | 10 (configurable) |
| **Scoring Method** | GPT-4o-mini only | Claude + Gemini + GPT evaluator |
| **Output Path** | `data/outputs/daily/` | `data/outputs/mini-daily/` |
| **Run ID Format** | `daily-YYYY-MM-DD` | `mini-daily-YYYY-MM-DD` |
| **Purpose** | Production | Experimental/Research |
| **Must-Reads Count** | 20 | 5 |

---

### Testing Locally

1. **Set up environment:**

```bash
export TRI_MODEL_MINI_DAILY=true
export CLAUDE_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."
export SPOTITEARLY_LLM_API_KEY="sk-..."
```

2. **Install dependencies:**

```bash
pip install anthropic google-generativeai
```

3. **Run with minimal window:**

```bash
python run_mini_daily.py \
  --lookback-hours 6 \
  --max-papers 3  # Start small for testing
```

4. **Check outputs:**

```bash
ls -la data/outputs/mini-daily/mini-daily-*/
cat data/outputs/mini-daily/mini-daily-*/report.md
```

---

### Troubleshooting

#### "Configuration validation failed: No reviewer API keys configured"

- Set at least one of `CLAUDE_API_KEY` or `GEMINI_API_KEY`

#### "No OpenAI API key for GPT evaluator"

- Set `SPOTITEARLY_LLM_API_KEY` or `OPENAI_API_KEY`

#### "Claude API call failed: rate limit"

- Reduce `--max-papers` or add delays between calls
- Gemini will continue working independently

#### "No reviews available to evaluate (both Claude and Gemini failed)"

- Check API keys and network connectivity
- Check logs for specific error messages
- If one reviewer consistently fails, the other can still run

#### Outputs not in Drive

- Check `ACITRACK_DRIVE_FOLDER_ID` is set
- Check `GOOGLE_APPLICATION_CREDENTIALS` points to valid JSON
- Use `--upload-drive` flag

---

### Next Steps

After tomorrow's experimental run:

1. **Analyze Results:**
   - Review `tri_model_reviews.json` for agreement patterns
   - Check `agreement_level` distribution
   - Identify cases where evaluator disagreed with reviewers

2. **Evaluate Cost:**
   - Claude API costs
   - Gemini API costs (usually lower/free)
   - GPT evaluator costs
   - Compare to standard GPT-only approach

3. **Assess Quality:**
   - Did multi-model reviews catch edge cases?
   - Were disagreements productive?
   - Did evaluator make good decisions?

4. **Decide on Production:**
   - If successful → Consider integrating into standard pipeline
   - If mixed → Refine prompts and retry
   - If unsuccessful → Document learnings and archive

---

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Mini-Daily Pipeline                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  1. Fetch (6h window) ──► 2. Dedupe ──► 3. Select (top 10) │
│                                                               │
│  4. Tri-Model Review Loop:                                   │
│     ┌──────────────────────────────────────────────┐        │
│     │  For each paper:                              │        │
│     │                                                │        │
│     │  ┌────────┐    ┌────────┐    ┌────────┐     │        │
│     │  │Claude  │    │Gemini  │    │  GPT   │     │        │
│     │  │Review  │───►│Review  │───►│Evaluate│     │        │
│     │  └────────┘    └────────┘    └────────┘     │        │
│     │       │             │              │         │        │
│     │       └─────────────┴──────────────┘         │        │
│     │                     │                         │        │
│     │              Final Decision                   │        │
│     └──────────────────────────────────────────────┘        │
│                                                               │
│  5. Generate Outputs:                                        │
│     - tri_model_reviews.json                                 │
│     - tri_model_final.json                                   │
│     - must_reads.json (top 5)                                │
│     - report.md                                              │
│     - manifest.json                                          │
│                                                               │
│  6. Upload to Drive (optional):                              │
│     MiniDaily/mini-daily-YYYY-MM-DD/                         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

### Files Added in This Branch

```
config/tri_model_config.py       # Configuration & feature flags
tri_model/                        # Tri-model system modules
├── __init__.py
├── prompts.py                    # Versioned prompts for all models
├── reviewers.py                  # Claude & Gemini implementations
└── evaluator.py                  # GPT evaluator implementation
run_mini_daily.py                 # Main runner script
.github/workflows/
└── mini-daily-tri-model.yml      # GitHub Actions workflow
TRI_MODEL_README.md               # This file
```

---

### Questions?

Contact the AciTrack team or review code in `tri_model/` module.
