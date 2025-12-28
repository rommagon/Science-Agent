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
- `type`: Source type (rss, pubmed)
- Additional fields depend on type (url for RSS, query for PubMed)

Example RSS source:
```yaml
sources:
  - name: Nature Cancer
    type: rss
    url: https://www.nature.com/natcancer.rss
```

Example PubMed source:
```yaml
  - name: The Lancet
    type: pubmed
    query: 'journal:"The Lancet"'
    retmax: 200
```

**Note on Historical Volumes:** Specific historical volumes or issues cited by leadership are treated as reference literature and are not ingested by the V1 "recent changes" engine. AciTrack V1 focuses exclusively on ongoing, forward-looking publication tracking.

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

## Google Drive Upload (Optional)

You can automatically upload the latest output files to a Google Drive folder by using the `--upload-drive` flag.

### Setup

#### 1. Create a Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable the Google Drive API:
   - Navigate to "APIs & Services" > "Library"
   - Search for "Google Drive API"
   - Click "Enable"
4. Create a service account:
   - Navigate to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "Service Account"
   - Give it a name (e.g., "acitrack-uploader")
   - Click "Create and Continue"
   - Skip granting roles (click "Continue")
   - Click "Done"
5. Create and download a JSON key:
   - Click on the newly created service account
   - Go to the "Keys" tab
   - Click "Add Key" > "Create new key"
   - Choose "JSON" format
   - Click "Create" - the key file will be downloaded

#### 2. Share Your Google Drive Folder

1. Create or navigate to the Google Drive folder where you want to upload files
2. Get the folder ID from the URL:
   - Example URL: `https://drive.google.com/drive/folders/1a2b3c4d5e6f7g8h9i0j`
   - Folder ID: `1a2b3c4d5e6f7g8h9i0j`
3. Share the folder with the service account:
   - Click "Share" on the folder
   - Add the service account email (found in your JSON key file, looks like `acitrack-uploader@your-project.iam.gserviceaccount.com`)
   - Give it "Editor" permissions
   - Click "Share"

**For Shared Drives (Team Drives):**
- The service account must be added as a member of the Shared Drive with "Content Manager" or "Manager" permissions

#### 3. Set Environment Variables

```bash
# Path to your service account JSON key file
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your-service-account-key.json"

# Google Drive folder ID where files will be uploaded
export ACITRACK_DRIVE_FOLDER_ID="1a2b3c4d5e6f7g8h9i0j"
```

Add these to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.) to make them permanent.

### Usage

Run with the `--upload-drive` flag to upload the latest outputs to Google Drive:

```bash
python run.py --since-days 7 --max-items-per-source 5 --upload-drive
```

This will upload three files to your configured Drive folder:
- `latest_report.md` - Full markdown report
- `latest_new.csv` - CSV export of new publications
- `latest_manifest.json` - Run provenance manifest

If files with the same names already exist in the folder, they will be updated (not duplicated).

### Verification

When you run with `--upload-drive`, the service account email will be printed:

```
📧 Service account: acitrack-uploader@your-project.iam.gserviceaccount.com
```

This helps verify you're using the correct credentials. After upload, you'll see links to the files in Google Drive.

### Troubleshooting

**"GOOGLE_APPLICATION_CREDENTIALS environment variable not set"**
- Make sure you've exported the environment variable in your current shell session

**"ACITRACK_DRIVE_FOLDER_ID environment variable not set"**
- Make sure you've exported the folder ID environment variable

**"Permission denied" or "404 not found"**
- Verify the service account email has been shared with the folder
- For Shared Drives, ensure the service account is a member of the Shared Drive

**"File not found" errors**
- The latest pointer files must exist before upload. Run the pipeline at least once without `--upload-drive` first.

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

## Troubleshooting

### urllib3 LibreSSL Warning

If you see warnings about `urllib3 v2` and `LibreSSL` compatibility:

```bash
# Check your urllib3 version
python -c "import urllib3; print(urllib3.__version__)"
```

The version should be `1.x`. If it shows `2.x`:

1. Delete and recreate your virtual environment
2. Reinstall dependencies: `pip install -r requirements.txt`

The requirements.txt pins `urllib3<2` and `requests<2.32` to maintain compatibility with macOS LibreSSL.

## Development Status

This is V1 - a focused implementation with core functionality: ingestion, summarization, change detection, commercial enrichment, and reporting.

## Development Workflow

### Branching Strategy

This repository uses a three-tier branching model:

- **`main`** - Production branch
  - Stable, autonomous, production-ready code
  - Weekly GitHub Actions runs execute on this branch
  - Snapshot state persists here via automated commits
  - Google Drive uploads occur from this branch
  - **Never break main** - all changes must be tested before merging

- **`dev`** - Integration/staging branch
  - Integration point for feature development
  - All new work should branch from `dev`
  - No scheduled workflows run on this branch
  - Testing ground before promoting to `main`

- **`feature/*`** - Short-lived feature branches
  - Branch from `dev` for new features or fixes
  - Merge back to `dev` when complete
  - Delete after merging

### Workflow

1. **Starting new work:**
   ```bash
   git checkout dev
   git pull origin dev
   git checkout -b feature/your-feature-name
   ```

2. **Completing work:**
   ```bash
   # From your feature branch
   git checkout dev
   git pull origin dev
   git merge feature/your-feature-name
   git push origin dev
   ```

3. **Promoting to production:**
   ```bash
   # Only when dev is stable and tested
   git checkout main
   git pull origin main
   git merge dev
   git push origin main
   ```

### Important Notes

- Scheduled GitHub Actions workflows only run on `main`
- The snapshot file (`data/snapshots/latest.json`) is only persisted from `main`
- Local testing can be done on any branch without affecting production state
- Use `--reset-snapshot` for testing to avoid polluting the production snapshot
