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

## Development Status

This is the initial scaffold. Core functionality (fetching, summarization, change detection, reporting) is currently stubbed and will be implemented in future iterations.
