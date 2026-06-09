# Quick Start: Mini-Daily Tri-Model Run

## Tomorrow's Run - Quick Reference

### GitHub Actions (Recommended) ⚡

1. Go to: https://github.com/YOUR_ORG/acitracker_v1/actions
2. Click: **"Mini-Daily Tri-Model Run"**
3. Click: **"Run workflow"** button
4. Select branch: `feature/tri-model-mini-daily`
5. Use defaults:
   - Lookback hours: `6`
   - Max papers: `10`
   - Upload to Drive: ✅ checked
6. Click: **"Run workflow"**
7. Wait ~5-10 minutes
8. Download artifacts when complete

### Required Secrets

Make sure these are configured in GitHub Settings > Secrets:

```
CLAUDE_API_KEY              # Anthropic API key
GEMINI_API_KEY              # Google Gemini API key
SPOTITEARLY_LLM_API_KEY     # OpenAI API key
ACITRACK_DRIVE_FOLDER_ID    # (optional) Drive folder ID
GOOGLE_APPLICATION_CREDENTIALS_JSON  # (optional) Google service account JSON
```

---

## Local Run (Alternative)

### Setup (One-Time)

```bash
# Checkout branch
git checkout feature/tri-model-mini-daily

# Install dependencies
pip install anthropic google-generativeai

# Set API keys
export TRI_MODEL_MINI_DAILY=true
export CLAUDE_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."
export SPOTITEARLY_LLM_API_KEY="sk-..."
```

### Run

```bash
python run_mini_daily.py --lookback-hours 6 --max-papers 10
```

### Check Outputs

```bash
# Find latest run
ls -la data/outputs/mini-daily/

# View must-reads report
cat data/outputs/mini-daily/mini-daily-*/report.md

# View raw data
jq '.must_reads[0]' data/outputs/mini-daily/mini-daily-*/must_reads.json
```

---

## What to Expect

**Runtime:** ~5-10 minutes for 10 papers

**Output Files:**
- `tri_model_reviews.json` - Raw reviews from Claude + Gemini
- `tri_model_final.json` - GPT evaluator decisions
- `must_reads.json` - Top 5 papers
- `report.md` - Human-readable summary
- `manifest.json` - Run metadata

**Drive Folder:**
- `MiniDaily/mini-daily-YYYY-MM-DD/` - All output files

**Cost:** ~$0.17 (very cheap for experiment)

---

## Troubleshooting

### "No reviewer API keys configured"
→ Set `CLAUDE_API_KEY` or `GEMINI_API_KEY`

### "No OpenAI API key for GPT evaluator"
→ Set `SPOTITEARLY_LLM_API_KEY`

### "Configuration validation failed"
→ Check all required API keys are set

### Workflow not appearing
→ Make sure you're on branch `feature/tri-model-mini-daily`

---

## After the Run

### Review Results

1. **Check agreement levels:**
   ```bash
   jq '[.final_decisions[].agreement_level] | group_by(.) | map({level: .[0], count: length})' \
     data/outputs/mini-daily/mini-daily-*/tri_model_final.json
   ```

2. **See disagreements:**
   ```bash
   jq '.final_decisions[] | select(.agreement_level == "low") | {title, claude_score, gemini_score, final_score, disagreements}' \
     data/outputs/mini-daily/mini-daily-*/tri_model_final.json
   ```

3. **Read must-reads:**
   ```bash
   cat data/outputs/mini-daily/mini-daily-*/report.md
   ```

### Share Results

- Download artifacts from GitHub Actions
- Or share files from `data/outputs/mini-daily/mini-daily-YYYY-MM-DD/`
- Or share Drive link: `Drive/MiniDaily/mini-daily-YYYY-MM-DD/`

---

## Full Documentation

- **Setup & Usage:** `TRI_MODEL_README.md`
- **Implementation Details:** `TRI_MODEL_SUMMARY.md`
- **Architecture:** See "Architecture Diagram" in README

---

## Emergency Stop

If something goes wrong:

```bash
# Stop workflow (GitHub Actions)
# → Go to workflow run page, click "Cancel workflow"

# Or locally: Ctrl+C

# Clean up (optional)
rm -rf data/outputs/mini-daily/mini-daily-*
```

Standard daily/weekly pipeline is **completely unaffected**.

---

**Ready to run! 🚀**
