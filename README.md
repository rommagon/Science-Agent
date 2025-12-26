# acitrack-v1

A Python-based publication tracker for cancer research. Fetches, summarizes, and tracks changes in publications from multiple sources.

## Project Structure

```
acitrack-v1/
├── run.py                      # Main CLI entrypoint
├── config/sources.yaml         # Source configuration
├── ingest/fetch.py            # Fetch publications from sources
├── summarize/summarize.py     # Generate summaries
├── diff/detect_changes.py     # Detect changes in publications
├── output/report.py           # Generate reports
├── data/                      # Data directory
│   ├── raw/                   # Raw fetched data
│   ├── summaries/             # Generated summaries
│   └── snapshots/             # Publication snapshots
├── acitrack_types.py          # Shared data types
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the tracker with default settings (last 7 days):
```bash
python run.py
```

### Demo Mode

Try the tracker quickly with a small dataset:
```bash
python run.py --reset-snapshot --since-days 7 --max-items-per-source 5
```

This resets the snapshot (marks all items as NEW), fetches the last 7 days, and limits output to 5 items per source for a quick test run.

### Options

#### Time Range
- `--since-days N`: Fetch publications from the last N days (default: 7)
- `--since-date YYYY-MM-DD`: Fetch publications since this specific date (overrides --since-days)

#### Output Control
- `--max-items-per-source N`: Maximum items to include per source in report/output (still ingests all items)
- `--max-new-to-summarize N`: Maximum NEW items to summarize (default: 200). Most recent items by date are prioritized.
- `--max-new-to-enrich N`: Maximum NEW items to enrich with commercial signals (default: 500). Most recent items by date are prioritized.

#### Source Filtering
- `--only-sources NAMES`: Comma-separated list of source names to include (run only these sources)
- `--exclude-sources NAMES`: Comma-separated list of source names to exclude (skip these sources)

#### Configuration
- `--config PATH`: Path to sources configuration file (default: config/sources.yaml)
- `--outdir PATH`: Output directory for data (default: data)
- `--reset-snapshot`: Delete snapshot before running (all items will be marked as NEW)

### Examples

Fetch publications from the last 30 days:
```bash
python run.py --since-days 30
```

Use a custom configuration file:
```bash
python run.py --config my-sources.yaml
```

Limit summarization for cost control:
```bash
python run.py --max-new-to-summarize 50 --max-new-to-enrich 100
```

Run only specific sources:
```bash
python run.py --only-sources "Nature Cancer,PubMed - cancer (broad)"
```

## Configuration

Edit `config/sources.yaml` to configure publication sources. Each source requires:
- `name`: Human-readable source name
- `type`: Source type (rss, web, api)
- `url`: Source URL

Example:
```yaml
sources:
  - name: Nature Cancer
    type: rss
    url: https://www.nature.com/subjects/cancer/rss
```

## Running Weekly

### Automated Runs with GitHub Actions

The repository includes a GitHub Actions workflow that runs acitrack automatically every Saturday at 00:00 UTC.

#### Enabling the Weekly Schedule

The workflow is defined in `.github/workflows/weekly_acitrack.yml` and will run automatically once pushed to your repository. The schedule can be modified by editing the cron expression in the workflow file.

#### Manual Trigger

You can manually trigger a run from the GitHub Actions tab:
1. Go to the "Actions" tab in your repository
2. Select "Weekly AciTrack Run" from the workflows list
3. Click "Run workflow"
4. Optionally configure:
   - Number of days to look back (default: 7)
   - Max items per source (default: 10)

#### Setting Up Secrets (Optional)

To enable AI-powered summaries in automated runs, add your OpenAI API key as a repository secret:

1. Go to Settings > Secrets and variables > Actions
2. Click "New repository secret"
3. Name: `OPENAI_API_KEY`
4. Value: Your OpenAI API key

**Note:** The workflow will run successfully even without this secret, but will use stub summaries instead of real AI-generated ones.

#### Accessing Results

After each workflow run:
- Go to the "Actions" tab and select the completed run
- Download the artifacts containing:
  - `*_report.md` - Full markdown report
  - `*_new.csv` - CSV export of new publications
  - `*_manifest.json` - Run provenance manifest
  - `latest_*` - Latest pointer files

#### Latest Pointers

After every successful run (local or automated), the following "latest" pointer files are created:
- `data/output/latest_report.md` - Copy of the most recent report
- `data/output/latest_new.csv` - Copy of the most recent new publications CSV
- `data/output/latest_manifest.json` - Copy of the most recent manifest

These files make it easy to access the most recent results without knowing the specific run ID.

## Testing

### Running Tests

The project includes a minimal regression test suite using pytest:

```bash
pytest
```

Run with verbose output:
```bash
pytest -v
```

Run specific test file:
```bash
pytest tests/test_compute_id.py
```

### Test Coverage

The test suite covers:
- **ID Generation** (`test_compute_id.py`): Deterministic publication ID generation
- **Change Detection** (`test_snapshot_diff.py`): First run all NEW, subsequent runs detect UNCHANGED
- **Report Generation** (`test_report_generation.py`): Report file creation and count accuracy
- **Commercial Signals** (`test_commercial.py`): Valid schema returned for all inputs including empty
- **PubMed Date Parsing** (`test_pubmed_dates.py`): Handles YYYY, YYYY Mon, YYYY Mon DD, YYYY Mon-Mon, and seasonal dates (Winter, Spring, Summer, Fall)

## Development Status

This is V1 - a focused implementation with core functionality: ingestion, summarization, change detection, commercial enrichment, and reporting.
