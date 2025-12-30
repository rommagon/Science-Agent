# AciTrack MCP Server with Must Reads UI

This directory contains the Model Context Protocol (MCP) server for acitrack, integrating with OpenAI's Custom GPT and Apps SDK to provide interactive must-reads functionality.

## Architecture

```
mcp_server/
├── server.py           # MCP server with tool definitions
├── must_reads.py       # Must-reads ranking logic with AI reranker integration
├── ai_reranker.py      # OpenAI-based reranking module
├── rerank_cache.py     # SQLite cache for rerank results
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

### Optional AI Reranking (v1)

**New in this version:** The must-reads tool now supports optional AI-powered reranking using OpenAI's API. This provides:

- **Improved Ranking:** LLM evaluates publications for relevance to early cancer detection
- **Better Explanations:** AI-generated "why it matters" and key findings
- **Automatic Caching:** Rerank results are cached in SQLite to minimize API calls
- **Strict Fallback:** Works perfectly without `OPENAI_API_KEY` - falls back to heuristic scoring

**How it works:**
1. Heuristic ranking generates shortlist of top N candidates (default: 50)
2. If `OPENAI_API_KEY` is set and `use_ai=true`, shortlist is sent to OpenAI for reranking
3. Cached results are used when available (keyed by pub_id + rerank_version)
4. If AI call fails or key is missing, heuristic scores are used
5. Final results include both heuristic and LLM scores

**No Breaking Changes:**
- Works without `OPENAI_API_KEY` (heuristic-only mode)
- Does NOT require API key in GitHub Actions
- Database schema auto-migrates (v2 → v3)
- Existing pipeline (run.py, snapshots, Drive) untouched

### MCP Tool: `get_must_reads`

Retrieves and ranks publications from the acitrack database using heuristic scoring with optional AI reranking.

**Parameters:**
- `since_days` (int, optional): Number of days to look back (default: 7, min: 1, max: 90)
- `limit` (int, optional): Maximum number of results (default: 10, min: 1, max: 50)
- `use_ai` (bool, optional): Enable AI reranking if `OPENAI_API_KEY` is available (default: true)
- `rerank_max_candidates` (int, optional): Max candidates to pass to AI reranker (default: 50, min: 10, max: 200)

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
      "score_total": 0,
      "score_components": {
        "heuristic": 0,
        "llm": 0 | null
      },
      "explanation": "string (1 sentence)",
      "why_it_matters": "string (1-2 lines)",
      "key_findings": ["string", "string", "string"]
    }
  ],
  "generated_at": "ISO8601",
  "window_days": 7,
  "total_candidates": 0,
  "used_ai": false,
  "rerank_version": "v1" | null
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

### 4. (Optional) Set OPENAI_API_KEY for AI Reranking

```bash
# Set your OpenAI API key (optional)
export OPENAI_API_KEY="sk-..."
```

**Note:** The tool works perfectly without this key - it will use heuristic-only ranking.

## Testing

### Quick Test Script

Use the provided test script to verify both heuristic and AI-enabled modes:

```bash
# Test without AI (always works)
python3 tools/check_must_reads.py

# Test with AI (requires OPENAI_API_KEY)
export OPENAI_API_KEY="sk-..."
python3 tools/check_must_reads.py
```

**Output:** The script will:
1. Run heuristic-only mode (`use_ai=False`)
2. Run AI-enabled mode (`use_ai=True`)
3. Compare rankings
4. Save results to `data/output/must_reads_*.json`

### Acceptance Tests

**Without OPENAI_API_KEY:**
```bash
unset OPENAI_API_KEY
python3 tools/check_must_reads.py
```

Expected:
- ✅ Tool returns list of must-reads
- ✅ No crash or errors
- ✅ `used_ai: false` in output
- ✅ `score_components.llm: null`

**With OPENAI_API_KEY:**
```bash
export OPENAI_API_KEY="sk-..."
python3 tools/check_must_reads.py
```

Expected:
- ✅ Rankings differ from heuristic-only
- ✅ `used_ai: true` in output
- ✅ `score_components.llm` has values
- ✅ Better explanations in `why_it_matters` and `key_findings`

**Cache Verification:**
```bash
# Run twice with AI enabled
export OPENAI_API_KEY="sk-..."
python3 tools/check_must_reads.py
python3 tools/check_must_reads.py
```

Expected:
- ✅ Second run is faster (cached results)
- ✅ Check SQLite: `SELECT COUNT(*) FROM must_reads_rerank_cache;`

## Testing (Original)

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
