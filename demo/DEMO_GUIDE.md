# AciTrack V1 Demo Guide

## What V1 Does

AciTrack V1 is an **automated publication tracking and monitoring system** for cancer research. It follows a simple pipeline:

### Pipeline: Trigger → Ingest → Summarize → Diff → Report

1. **Trigger**
   - Manual: Run `python run.py` locally
   - Automated: GitHub Actions runs every Saturday at 00:00 UTC

2. **Ingest**
   - Fetches publications from multiple sources:
     - Nature Cancer (RSS)
     - Science News (RSS)
     - bioRxiv preprints (RSS)
     - medRxiv preprints (RSS)
     - PubMed (API)
   - Normalizes publication data (title, authors, date, URL, abstract)
   - Configurable via `config/sources.yaml`

3. **Summarize** (with safety caps)
   - Generates AI-powered summaries for NEW publications:
     - One-liner summary
     - 3-5 essence bullets highlighting key points
   - Default cap: 200 NEW items (configurable with `--max-new-to-summarize`)
   - Uses caching to avoid re-summarizing

4. **Enrich** (commercial signals)
   - Scans NEW publications for commercial indicators:
     - Sponsor signals (pharmaceutical/biotech company funding)
     - Company affiliation signals (authors from industry)
   - Default cap: 500 NEW items (configurable with `--max-new-to-enrich`)
   - Extracts company names and evidence snippets

5. **Diff** (change detection)
   - Compares against snapshot from previous run
   - Marks publications as:
     - **NEW**: Not seen in previous snapshot
     - **UNCHANGED**: Already in snapshot
   - Snapshot automatically updated after each run

6. **Report**
   - Generates `<run_id>_report.md`: Full markdown report
   - Generates `<run_id>_new.csv`: CSV export of NEW publications
   - Generates `<run_id>_manifest.json`: Run provenance
   - Creates "latest" pointers for easy access

## What V1 Explicitly Does NOT Do

**No Relevance Scoring**
- V1 does not score or rank publications by relevance
- All publications from configured sources are treated equally
- No filtering based on clinical trials, specific cancer types, or research phases
- No prioritization algorithm

**No Content Analysis**
- Does not extract structured data (endpoints, cohort sizes, p-values)
- Does not classify publication types (clinical trial, basic research, review)
- Does not identify specific interventions or outcomes

**No Alerting**
- Does not send notifications or emails
- Does not flag "important" publications
- No real-time monitoring (runs on schedule only)

**No Data Storage Beyond Snapshots**
- Does not maintain a searchable database
- Historical data is in raw JSON files only
- No query interface or API

## How to Interpret the Report

### Report Structure (`latest_report.md`)

```markdown
# AciTrack Report
Run ID: <timestamp>_<hash>

## Summary
- Total fetched: X publications
- NEW: Y publications
- UNCHANGED: Z publications

## NEW Publications by Source

### Source Name (X new)
#### Title of Publication
**Authors:** List of authors
**Date:** YYYY-MM-DD
**URL:** [Link]

**Summary:**
One-liner description of the publication.

**Key Points:**
- Essence bullet 1
- Essence bullet 2
- Essence bullet 3

**Commercial Signals:**
- 🏢 Company Affiliation: [Company names if detected]
- 💰 Sponsor Signal: [Sponsor names if detected]
```

### CSV Export (`latest_new.csv`)

Columns include:
- Basic metadata: ID, Title, Authors, Source, Date, URL
- Summaries: one_liner, essence_bullets (JSON array)
- Commercial signals: has_sponsor_signal, sponsor_names, company_affiliation_signal, company_names, evidence_snippets

### Understanding "NEW" vs "UNCHANGED"

- **First run** (or after `--reset-snapshot`): All items are NEW
- **Subsequent runs**: Only publications not seen before are NEW
- If you run twice with the same date range, second run should show 0 NEW items

### Safety Caps

When NEW items exceed caps:
```
⚠️  WARNING: 255 NEW items exceed summarization cap of 200
   Summarizing only the 200 most recent items by date.
```

- **Most recent items by date** are prioritized
- Skipped items marked with: `one_liner = "Summary skipped due to cap."`
- All items still appear in `changes.json` archive

## Weekly Scheduling (GitHub Actions)

### Automated Runs

The workflow `.github/workflows/weekly_acitrack.yml` runs automatically:
- **Schedule**: Every Saturday at 00:00 UTC
- **Parameters**: `--since-days 7 --max-items-per-source 10`
- **Outputs**: Uploaded as GitHub Actions artifacts (90-day retention)

### Setting Up Automated Runs

1. **Push to GitHub**: Workflow activates automatically
2. **Add API Key** (optional for real summaries):
   - Go to Settings → Secrets and variables → Actions
   - Add secret: `OPENAI_API_KEY`
3. **View Results**:
   - Go to Actions tab
   - Select completed run
   - Download artifacts containing reports

### Manual Trigger

From GitHub Actions tab:
1. Click "Run workflow"
2. Configure days to look back (default: 7)
3. Configure max items per source (default: 10)
4. Run and download results

## Next Steps

After reviewing the demo outputs:

1. **Adjust sources**: Edit `config/sources.yaml` to add/remove publication sources
2. **Customize caps**: Use `--max-new-to-summarize` and `--max-new-to-enrich` for cost control
3. **Filter sources**: Use `--only-sources` or `--exclude-sources` for focused runs
4. **Enable automation**: Push to GitHub and add `OPENAI_API_KEY` secret
5. **Review regularly**: Check `latest_report.md` after each scheduled run

## Cost Considerations

- **Summarization**: Uses OpenAI API (costs apply per item summarized)
- **Commercial enrichment**: Pattern matching only (no API costs)
- **Default caps**: 200 summaries, 500 enrichments per run
- **Weekly automation**: ~7 days × 5 sources × variable items = budget accordingly

## Questions?

- Run `python run.py --help` for all available options
- Check `README.md` for full documentation
- Review source code for implementation details
