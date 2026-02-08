# Canonical URLs and Semantic Search

This document describes the canonical URL resolution and semantic search embedding features added to acitracker.

## Overview

Two new features have been added to improve publication handling:

1. **Canonical URLs**: Standardized URLs for publications based on DOI, PMID, or other identifiers
2. **Semantic Search Embeddings**: Vector embeddings for semantic similarity search

## Schema Changes (Version 8)

### New Columns in `publications` Table

| Column | Type | Description |
|--------|------|-------------|
| `canonical_url` | TEXT | Normalized canonical URL for the publication |
| `doi` | TEXT | Digital Object Identifier (if available) |
| `pmid` | TEXT | PubMed ID (if available) |
| `source_type` | TEXT | Source type (pubmed, biorxiv, medrxiv, arxiv, nature, etc.) |

### New Table: `publication_embeddings`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key (auto-increment) |
| `publication_id` | TEXT | Foreign key to publications.id |
| `embedding_model` | TEXT | Name of embedding model used |
| `embedding_dim` | INTEGER | Dimension of embedding vector |
| `embedding` | BLOB | Embedding vector as bytes |
| `content_hash` | TEXT | SHA256 hash of input text |
| `created_at` | TIMESTAMP | Creation timestamp |

**Unique constraint**: `(publication_id, embedding_model, content_hash)`

## Canonical URL Resolution

### Priority Order

1. **DOI**: If available, construct `https://doi.org/<doi>`
2. **PMID**: If available, construct `https://pubmed.ncbi.nlm.nih.gov/<pmid>/`
3. **arXiv**: If arXiv ID found, construct `https://arxiv.org/abs/<id>`
4. **Existing URL**: Normalize existing URL (https, remove tracking params)

### URL Normalization

- Upgrade HTTP to HTTPS
- Lowercase hostname
- Remove tracking parameters (utm_*, fbclid, gclid, etc.)
- Remove URL fragments
- Strip trailing slashes

### Source Type Detection

Automatically detected from URL patterns and source names:
- `pubmed` - PubMed/NCBI
- `biorxiv` - bioRxiv
- `medrxiv` - medRxiv
- `arxiv` - arXiv
- `nature` - Nature journals
- `science` - Science/AAAS
- `cell` - Cell Press
- `lancet` - The Lancet
- `nejm` - New England Journal of Medicine
- `jama` - JAMA Network
- `rss` - Generic RSS (fallback)

## Semantic Search

### Embedding Model

Default: `text-embedding-3-small` (1536 dimensions)

Alternative models supported:
- `text-embedding-3-large` (3072 dimensions)
- `text-embedding-ada-002` (1536 dimensions)

### Embedding Text Construction

The embedding input text is constructed from:
```
<title>

<abstract or summary>

Journal: <venue or source>
Published: <publication_date>
```

### Content Hash

A SHA256 hash of the normalized (lowercase, trimmed) embedding text is stored.
This enables:
- Detecting when content changes
- Avoiding duplicate embedding generation
- Efficient cache invalidation

## Backfill Scripts

### Canonical URL Backfill

```bash
# Backfill all publications missing canonical URLs
python scripts/backfill_links.py

# Backfill only publications from the last 30 days
python scripts/backfill_links.py --since-days 30

# Preview changes without updating database
python scripts/backfill_links.py --dry-run --limit 100
```

**Arguments:**
- `--since-days N`: Only process publications from the last N days
- `--limit N`: Maximum number of publications to process
- `--dry-run`: Preview changes without updating database
- `--verbose`, `-v`: Enable verbose logging

### Embedding Backfill

```bash
# Backfill all publications missing embeddings
python scripts/backfill_embeddings.py

# Backfill only publications from the last 30 days
python scripts/backfill_embeddings.py --since-days 30

# Preview changes without generating embeddings
python scripts/backfill_embeddings.py --dry-run --limit 100

# Use a specific model
python scripts/backfill_embeddings.py --model text-embedding-3-large
```

**Arguments:**
- `--model MODEL`: Embedding model to use (default: text-embedding-3-small)
- `--since-days N`: Only process publications from the last N days
- `--limit N`: Maximum number of publications to process
- `--max-per-minute N`: Rate limit for API calls (default: 200)
- `--dry-run`: Preview changes without generating embeddings
- `--verbose`, `-v`: Enable verbose logging

## Environment Variables

### Required for Embeddings

One of the following must be set to generate embeddings:
- `SPOTITEARLY_LLM_API_KEY` - OpenAI API key (preferred)
- `OPENAI_API_KEY` - OpenAI API key (fallback)

### Optional

- `DATABASE_URL` - PostgreSQL connection URL (defaults to SQLite)

## Daily Runner Integration

The tri-model daily runner (`run_tri_model_daily.py`) automatically:

1. **Phase 1.7**: Enriches publications with canonical URLs
   - Extracts DOI, PMID from text/URLs
   - Detects source type
   - Resolves and normalizes canonical URL

2. **Phase 1.8**: Generates embeddings
   - Only if OpenAI API key is configured
   - Non-blocking: failures don't stop the pipeline
   - Logs success/failure counts

## Semantic Search API

### Basic Search

```python
from acitrack.semantic_search import search_publications

# Search for publications similar to a query
results = search_publications(
    query="early cancer detection using liquid biopsy",
    top_k=10,
    since_days=30,
)

for result in results:
    print(f"{result['similarity']:.3f}: {result['title']}")
```

### In-Memory Index

For faster repeated searches:

```python
from acitrack.semantic_search import SemanticSearchIndex

# Load index once
index = SemanticSearchIndex(
    embedding_model="text-embedding-3-small",
    since_days=90,
)

# Search multiple times
results1 = index.search("liquid biopsy", top_k=5)
results2 = index.search("ctDNA biomarkers", top_k=5)
```

### Utility Functions

```python
from acitrack.semantic_search import (
    build_embedding_text,    # Build text for embedding
    compute_content_hash,    # Compute SHA256 hash
    embed_text,              # Embed single text
    embed_texts,             # Embed multiple texts
    cosine_similarity,       # Compute similarity
    embedding_to_bytes,      # Convert to bytes for storage
    bytes_to_embedding,      # Convert from bytes
)
```

## Limitations

1. **Rate Limits**: OpenAI embedding API has rate limits. The backfill script respects `--max-per-minute` (default: 200).

2. **Cost**: Embedding generation costs API credits. `text-embedding-3-small` is the most cost-effective option.

3. **No pgvector**: PostgreSQL does not use pgvector for similarity search. All similarity computation is done in Python. For large datasets, consider implementing pgvector support.

4. **Missing API Key**: If the OpenAI API key is not configured, embedding generation is skipped with a warning. The rest of the pipeline continues normally.

## Testing

```bash
# Run canonical URL tests
python -m pytest tests/test_canonical_url.py -v

# Run semantic search tests
python -m pytest tests/test_semantic_search.py -v
```
