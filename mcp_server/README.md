# AciTrack MCP Server with Must Reads UI

This directory contains the Model Context Protocol (MCP) server for acitrack, integrating with OpenAI's Custom GPT and Apps SDK to provide interactive must-reads functionality.

## Architecture

```
mcp_server/
├── server.py           # MCP server with tool definitions
├── must_reads.py       # Must-reads ranking logic
├── ui_config.json      # UI widget registration
└── ui/                 # OpenAI Apps SDK UI widget
    ├── package.json
    ├── vite.config.ts
    ├── src/
    │   ├── MustReadsWidget.tsx
    │   └── index.tsx
    └── dist/           # Built widget (generated)
```

## Features

### MCP Tool: `get_must_reads`

Retrieves and ranks publications from the acitrack database using a heuristic scoring algorithm.

**Parameters:**
- `since_days` (int, optional): Number of days to look back (default: 7, min: 1, max: 90)
- `limit` (int, optional): Maximum number of results (default: 10, min: 1, max: 50)

**Returns:** JSON with structure:
```json
{
  "must_reads": [
    {
      "id": "string",
      "title": "string",
      "published_date": "ISO8601",
      "source": "string",
      "venue": "string",
      "url": "string",
      "why_it_matters": "string (1-2 lines)",
      "key_findings": ["string", "string", "string"],
      "rank_score": 0,
      "rank_reason": "string"
    }
  ],
  "generated_at": "ISO8601",
  "window_days": 7,
  "total_candidates": 0
}
```

### Ranking Heuristic

Publications are scored based on:

1. **Source Priority (0-100 points)**
   - Nature Cancer: 100
   - Science: 90
   - The Lancet: 80
   - BMJ: 70
   - bioRxiv/medRxiv: 60
   - Other sources: 10 (baseline)

2. **Recency (0-200 points)**
   - < 7 days: 200 points
   - < 14 days: 150 points
   - < 30 days: 100 points
   - Older: 50 points

3. **Keyword Relevance (0-300 points)**
   - 3+ keyword matches: 300 points
   - 2 matches: 200 points
   - 1 match: 100 points
   - Keywords: screening, biomarker, early detection, ctDNA, methylation, liquid biopsy, etc.

**Total Score Range:** 0-600 points

### UI Widget

The Must Reads widget provides an interactive card-based interface with:

- **Open Button:** Opens publication in external browser
- **Explain Why Button:** Sends follow-up message to GPT asking for deeper explanation
- **Refresh Button:** Re-runs the tool with current parameters
- **Save Toggle:** Persists saved publication IDs (minimal state)

**Widget State:**
- Saved IDs are stored via `window.openai.setWidgetState()`
- State is minimal to keep payload small

## Setup and Installation

### 1. Install Python Dependencies

The MCP server requires the `mcp` package:

```bash
# From project root
python3 -m pip install mcp
```

### 2. Build the UI Widget

```bash
# Navigate to UI directory
cd mcp_server/ui

# Install dependencies
npm install

# Build the widget
npm run build
```

This generates `ui/dist/must-reads-widget.js`.

### 3. Run the MCP Server Locally

```bash
# From project root
python3 -m mcp_server.server
```

The server communicates via stdio and expects JSON-RPC messages.

## Testing

### Test Tool Output Shape

You can test the tool output directly:

```bash
# From project root
python3 << 'EOF'
from mcp_server.must_reads import get_must_reads_from_db
import json

result = get_must_reads_from_db(since_days=7, limit=10)
print(json.dumps(result, indent=2))
EOF
```

### Test UI Widget Locally

```bash
# From mcp_server/ui
npm run dev
```

This starts a Vite dev server at `http://localhost:5173` with sample data and mocked OpenAI SDK functions.

**What you should see:**
- Must Reads cards with sample publications
- "Open" button opens links
- "Explain why" button shows alert with follow-up prompt
- "Refresh" button shows alert with tool call params
- "Save" toggle persists to localStorage

## Integration with Custom GPT

### 1. Configure MCP in Custom GPT

Add the acitrack MCP server to your Custom GPT configuration:

```json
{
  "mcp_servers": [
    {
      "name": "acitrack",
      "command": "python3",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/acitracker_v1"
    }
  ]
}
```

### 2. Register UI Widget

The widget is automatically registered via `ui_config.json`. When the `get_must_reads` tool returns data, the Custom GPT will render the widget.

### 3. Demo in ChatGPT

Example prompts:

```
"Show me the must-read publications from the last week"
"What are the top cancer research papers from the last 30 days?"
"Refresh the must-reads list"
```

The GPT will:
1. Call the `get_must_reads` tool
2. Render the Must Reads widget
3. Allow interaction (Open, Explain, Refresh, Save)

## Data Sources

The `get_must_reads` tool uses two data sources in priority order:

1. **SQLite Database** (`data/db/acitrack.db`)
   - Primary source
   - Fast queries with indexed fields
   - Requires database to be populated by pipeline

2. **Fallback: Latest Run Outputs** (`data/raw/*_publications.json`)
   - Used if database is unavailable
   - Reads most recent publications file
   - Less efficient but ensures functionality

3. **Empty State**
   - Returns friendly empty state if no data available
   - Prompts user to run pipeline or extend time window

## Logging

The MCP server logs to stderr:

```
2025-12-30 10:30:00 - mcp_server.server - INFO - Starting acitrack MCP server...
2025-12-30 10:30:05 - mcp_server.must_reads - INFO - get_must_reads called with since_days=7, limit=10
```

## Constraints and Design Decisions

### No Breaking Changes

The MCP server and must-reads functionality are **purely additive**:
- Existing pipeline (weekly runs, Drive upload, snapshots, reports) is untouched
- Database queries are read-only
- Fallback mechanism ensures graceful degradation

### Secrets Management

- No API keys or secrets in code
- Relies on existing environment variable patterns
- MCP server runs in user's environment with existing credentials

### Deterministic Ranking

- Ranking algorithm is transparent and debuggable
- Scores and reasons are returned with each result
- Easy to evolve by adjusting weights in `must_reads.py`

## Future Enhancements

Potential improvements (not in scope for v1):

1. **User-Defined Filters:** Allow filtering by source, keyword, date range
2. **AI-Powered Ranking:** Use embedding similarity or LLM scoring
3. **Saved Collections:** Persist full publication data, not just IDs
4. **Export to PDF/Email:** Generate downloadable must-reads report
5. **Trend Analysis:** Show week-over-week changes in must-reads

## Troubleshooting

**"Database not found" warning:**
- Run the main pipeline at least once to populate the database
- Or ensure `data/db/acitrack.db` exists

**"No must-reads found":**
- Check that publications exist in the specified time window
- Try extending `since_days` parameter
- Verify database has recent data

**Widget not rendering:**
- Ensure UI is built (`npm run build` in `mcp_server/ui`)
- Check `ui_config.json` paths are correct
- Verify Custom GPT MCP configuration

**Tool call fails:**
- Check MCP server logs (stderr)
- Verify server is running and accessible
- Test tool locally with the test script above
