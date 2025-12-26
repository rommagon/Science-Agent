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

### Options

- `--since-days N`: Fetch publications from the last N days (default: 7)
- `--config PATH`: Path to sources configuration file (default: config/sources.yaml)
- `--outdir PATH`: Output directory for data (default: data)

### Examples

Fetch publications from the last 30 days:
```bash
python run.py --since-days 30
```

Use a custom configuration file:
```bash
python run.py --config my-sources.yaml
```

Specify output directory:
```bash
python run.py --outdir /path/to/output
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

## Development Status

This is the initial scaffold. Core functionality (fetching, summarization, change detection, reporting) is currently stubbed and will be implemented in future iterations.
