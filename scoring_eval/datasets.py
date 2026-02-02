"""Dataset loading and normalization for scoring evaluation.

This module provides functions to:
- Load Udi ground truth files (CSV/JSON)
- Load calibration survey files (CSV/JSON)
- Normalize to canonical schema
- Match publications across datasets by ID, DOI, PMID, or title
"""

import csv
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# Canonical schema for evaluation items
# {
#     "publication_id": str | None,
#     "title": str,
#     "doi": str | None,
#     "pmid": str | None,
#     "url": str | None,
#     "abstract": str | None,
#     "human_labels": [
#         {
#             "source": "udi" | "survey",
#             "rater": str | None,
#             "rating_0_3": int,
#             "rationale": str | None
#         }
#     ]
# }


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    """Normalize DOI to standard format.

    Args:
        doi: DOI string (may include prefix like 'doi:', URL, etc.)

    Returns:
        Normalized DOI (e.g., '10.1234/example') or None
    """
    if not doi:
        return None

    doi = str(doi).strip()

    # Remove common prefixes
    prefixes = [
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
        "DOI:",
    ]
    for prefix in prefixes:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break

    # Validate DOI format (should start with 10.)
    if doi and doi.startswith("10."):
        return doi.lower().strip()

    return None


def normalize_pmid(pmid: Optional[str]) -> Optional[str]:
    """Normalize PMID to standard format.

    Args:
        pmid: PMID string (may include prefix like 'PMID:', URL, etc.)

    Returns:
        Normalized PMID (digits only) or None
    """
    if not pmid:
        return None

    pmid = str(pmid).strip()

    # Remove common prefixes
    prefixes = [
        "https://pubmed.ncbi.nlm.nih.gov/",
        "http://pubmed.ncbi.nlm.nih.gov/",
        "PMID:",
        "pmid:",
    ]
    for prefix in prefixes:
        if pmid.startswith(prefix):
            pmid = pmid[len(prefix):]
            break

    # Extract digits only
    digits = re.sub(r"[^\d]", "", pmid)

    if digits:
        return digits

    return None


def normalize_title(title: Optional[str]) -> str:
    """Normalize title for fuzzy matching.

    Args:
        title: Publication title

    Returns:
        Normalized title (lowercase, stripped, no extra whitespace)
    """
    if not title:
        return ""

    # Lowercase
    normalized = str(title).lower().strip()

    # Remove punctuation except essential ones
    normalized = re.sub(r"[^\w\s\-]", " ", normalized)

    # Collapse multiple whitespace
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized.strip()


def compute_title_hash(title: str) -> str:
    """Compute stable hash for normalized title.

    Args:
        title: Publication title (will be normalized)

    Returns:
        SHA256 hex digest of normalized title
    """
    normalized = normalize_title(title)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def extract_doi_from_url(url: Optional[str]) -> Optional[str]:
    """Extract DOI from URL if present.

    Args:
        url: Publication URL

    Returns:
        Extracted DOI or None
    """
    if not url:
        return None

    url = str(url)

    # Common DOI URL patterns
    patterns = [
        r"doi\.org/(10\.[^\s/]+/[^\s]+)",
        r"doi/(10\.[^\s/]+/[^\s]+)",
        r"(10\.\d{4,}/[^\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            doi = match.group(1)
            # Clean up trailing characters
            doi = re.sub(r"[/\s]+$", "", doi)
            return normalize_doi(doi)

    return None


def extract_pmid_from_url(url: Optional[str]) -> Optional[str]:
    """Extract PMID from URL if present.

    Args:
        url: Publication URL

    Returns:
        Extracted PMID or None
    """
    if not url:
        return None

    url = str(url)

    # PubMed URL pattern
    match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def normalize_canonical_url(url: Optional[str]) -> Optional[str]:
    """Normalize URL for canonical matching."""
    if not url:
        return None
    url = str(url).strip()
    if not url:
        return None
    return url.rstrip("/").lower()


def _load_json_file(file_path: Union[str, Path]) -> Any:
    """Load JSON file with error handling.

    Args:
        file_path: Path to JSON file

    Returns:
        Parsed JSON content

    Raises:
        ValueError: If file cannot be loaded
    """
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}")


def _load_csv_file(file_path: Union[str, Path]) -> List[Dict[str, str]]:
    """Load CSV file with error handling.

    Args:
        file_path: Path to CSV file

    Returns:
        List of row dicts

    Raises:
        ValueError: If file cannot be loaded
    """
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception as e:
        raise ValueError(f"Error reading CSV {path}: {e}")


def _detect_file_format(file_path: Union[str, Path]) -> str:
    """Detect file format from extension.

    Args:
        file_path: Path to file

    Returns:
        'json' or 'csv'

    Raises:
        ValueError: If format not recognized
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        return "json"
    elif suffix in [".csv", ".tsv"]:
        return "csv"
    else:
        raise ValueError(f"Unsupported file format: {suffix}")


def load_udi_ground_truth(
    file_path: Union[str, Path],
    rating_column: str = "rating",
    notes_column: str = "notes",
) -> List[Dict[str, Any]]:
    """Load Udi's ground truth rankings.

    Expected formats:
    - JSON: List of objects with title, doi, pmid, url, rating, notes
    - CSV: Columns for title, doi, pmid, url, rating, notes

    The rating should be on a 0-3 scale:
    - 3: Central/must-read (highest relevance)
    - 2: Highly relevant
    - 1: Somewhat relevant
    - 0: Not relevant

    Args:
        file_path: Path to ground truth file
        rating_column: Name of rating column/field
        notes_column: Name of notes/rationale column/field

    Returns:
        List of canonical schema items
    """
    file_format = _detect_file_format(file_path)

    if file_format == "json":
        data = _load_json_file(file_path)
    else:
        data = _load_csv_file(file_path)

    if not isinstance(data, list):
        raise ValueError(f"Expected list of items, got {type(data)}")

    items = []
    for idx, row in enumerate(data):
        # Handle both dict and string entries (udi_seeds.json has simple format)
        if isinstance(row, str):
            # Simple string entry, treat as URL/identifier
            item = {
                "publication_id": None,
                "title": "",
                "doi": normalize_doi(row) if row.startswith("10.") else None,
                "pmid": normalize_pmid(row) if row.isdigit() else None,
                "url": row if row.startswith("http") else None,
                "abstract": None,
                "human_labels": [
                    {
                        "source": "udi",
                        "rater": "udi",
                        "rating_0_3": 3,  # Assume central if just listed
                        "rationale": None,
                    }
                ],
            }
        elif isinstance(row, dict):
            # Extract identifiers
            doi = row.get("doi") or extract_doi_from_url(row.get("url"))
            pmid = row.get("pmid") or row.get("PMID") or extract_pmid_from_url(row.get("url"))

            # Handle udi_seeds.json format with type/value structure
            if "type" in row and "value" in row:
                item_type = row.get("type", "").lower()
                value = row.get("value", "")

                if item_type == "doi":
                    doi = normalize_doi(value)
                elif item_type == "pmid":
                    pmid = normalize_pmid(value)
                elif item_type == "url":
                    doi = doi or extract_doi_from_url(value)
                    pmid = pmid or extract_pmid_from_url(value)

                url = value if value.startswith("http") else row.get("url")
                title = row.get("title", "")

                item = {
                    "publication_id": row.get("id") or row.get("publication_id"),
                    "title": title,
                    "doi": normalize_doi(doi),
                    "pmid": normalize_pmid(pmid),
                    "url": url,
                    "abstract": row.get("abstract") or row.get("raw_text"),
                    "human_labels": [
                        {
                            "source": "udi",
                            "rater": "udi",
                            "rating_0_3": 3,  # Seeds are assumed central
                            "rationale": row.get(notes_column) or row.get("comment"),
                        }
                    ],
                }
            else:
                # Standard format
                # Parse rating
                rating_raw = row.get(rating_column) or row.get("rating_0_3") or row.get("relevance")
                try:
                    rating = int(float(str(rating_raw).strip())) if rating_raw else 3
                    rating = max(0, min(3, rating))  # Clamp to 0-3
                except (ValueError, TypeError):
                    logger.warning(f"Invalid rating '{rating_raw}' at row {idx}, defaulting to 3")
                    rating = 3

                item = {
                    "publication_id": row.get("id") or row.get("publication_id"),
                    "title": row.get("title", ""),
                    "doi": normalize_doi(doi),
                    "pmid": normalize_pmid(pmid),
                    "url": row.get("url"),
                    "abstract": row.get("abstract") or row.get("raw_text"),
                    "human_labels": [
                        {
                            "source": "udi",
                            "rater": "udi",
                            "rating_0_3": rating,
                            "rationale": row.get(notes_column),
                        }
                    ],
                }
        else:
            logger.warning(f"Skipping invalid row type at index {idx}: {type(row)}")
            continue

        items.append(item)

    logger.info(f"Loaded {len(items)} items from Udi ground truth: {file_path}")
    return items


def load_calibration_survey(
    file_path: Union[str, Path],
    rater_column: str = "rater",
    rating_column: str = "rating",
    rationale_column: str = "rationale",
) -> List[Dict[str, Any]]:
    """Load calibration survey with multiple raters.

    Expected formats:
    - JSON: List of objects with rater ratings per publication
    - CSV: Columns for publication identifiers + per-rater ratings

    Args:
        file_path: Path to survey file
        rater_column: Name of rater column/field
        rating_column: Name of rating column/field
        rationale_column: Name of rationale column/field

    Returns:
        List of canonical schema items (may have multiple labels per item)
    """
    file_format = _detect_file_format(file_path)

    if file_format == "json":
        data = _load_json_file(file_path)
    else:
        data = _load_csv_file(file_path)

    if not isinstance(data, list):
        raise ValueError(f"Expected list of items, got {type(data)}")

    # Group by publication to aggregate multiple rater labels
    pub_labels: Dict[str, Dict[str, Any]] = {}

    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            logger.warning(f"Skipping non-dict row at index {idx}")
            continue

        # Extract identifiers
        doi = row.get("doi") or extract_doi_from_url(row.get("url"))
        pmid = row.get("pmid") or row.get("PMID") or extract_pmid_from_url(row.get("url"))
        title = row.get("title", "")
        pub_id = row.get("id") or row.get("publication_id")

        # Create lookup key
        key = pub_id or normalize_doi(doi) or normalize_pmid(pmid) or compute_title_hash(title)
        if not key:
            logger.warning(f"Skipping row {idx} with no identifiable key")
            continue

        # Parse rating
        rating_raw = row.get(rating_column) or row.get("rating_0_3")
        try:
            rating = int(float(str(rating_raw).strip())) if rating_raw else None
            if rating is not None:
                rating = max(0, min(3, rating))
        except (ValueError, TypeError):
            logger.warning(f"Invalid rating '{rating_raw}' at row {idx}")
            rating = None

        if rating is None:
            continue

        # Get or create publication entry
        if key not in pub_labels:
            pub_labels[key] = {
                "publication_id": pub_id,
                "title": title,
                "doi": normalize_doi(doi),
                "pmid": normalize_pmid(pmid),
                "url": row.get("url"),
                "abstract": row.get("abstract") or row.get("raw_text"),
                "human_labels": [],
            }

        # Add label
        pub_labels[key]["human_labels"].append({
            "source": "survey",
            "rater": row.get(rater_column),
            "rating_0_3": rating,
            "rationale": row.get(rationale_column),
        })

        # Update identifiers if missing
        entry = pub_labels[key]
        if not entry["title"] and title:
            entry["title"] = title
        if not entry["doi"] and doi:
            entry["doi"] = normalize_doi(doi)
        if not entry["pmid"] and pmid:
            entry["pmid"] = normalize_pmid(pmid)
        if not entry["url"] and row.get("url"):
            entry["url"] = row.get("url")
        if not entry["abstract"] and (row.get("abstract") or row.get("raw_text")):
            entry["abstract"] = row.get("abstract") or row.get("raw_text")

    items = list(pub_labels.values())
    logger.info(f"Loaded {len(items)} items from calibration survey: {file_path}")
    return items


def normalize_to_canonical(
    items: List[Dict[str, Any]],
    source: str = "unknown",
) -> List[Dict[str, Any]]:
    """Ensure items conform to canonical schema.

    Args:
        items: List of items (may have varying schemas)
        source: Source name for labels without explicit source

    Returns:
        List of canonical schema items
    """
    normalized = []

    for item in items:
        # Extract identifiers
        doi = item.get("doi")
        pmid = item.get("pmid")
        url = item.get("url")

        if not doi and url:
            doi = extract_doi_from_url(url)
        if not pmid and url:
            pmid = extract_pmid_from_url(url)

        # Ensure human_labels exists
        labels = item.get("human_labels", [])
        if not labels:
            # Try to extract from flat fields
            rating = item.get("rating_0_3") or item.get("rating") or item.get("relevance")
            if rating is not None:
                try:
                    rating = int(float(str(rating).strip()))
                    rating = max(0, min(3, rating))
                    labels = [{
                        "source": source,
                        "rater": item.get("rater"),
                        "rating_0_3": rating,
                        "rationale": item.get("rationale") or item.get("notes"),
                    }]
                except (ValueError, TypeError):
                    labels = []

        canonical = {
            "publication_id": item.get("publication_id") or item.get("id"),
            "title": item.get("title", ""),
            "doi": normalize_doi(doi),
            "pmid": normalize_pmid(pmid),
            "url": url,
            "abstract": item.get("abstract") or item.get("raw_text"),
            "human_labels": labels,
        }

        normalized.append(canonical)

    return normalized


def match_publications(
    item1: Dict[str, Any],
    item2: Dict[str, Any],
) -> bool:
    """Check if two items refer to the same publication.

    Matching priority:
    1. publication_id (exact match)
    2. doi (normalized)
    3. pmid (normalized)
    4. title (normalized fuzzy match)

    Args:
        item1: First item
        item2: Second item

    Returns:
        True if items match
    """
    # Match by publication_id
    id1 = item1.get("publication_id")
    id2 = item2.get("publication_id")
    if id1 and id2 and id1 == id2:
        return True

    # Match by DOI
    doi1 = normalize_doi(item1.get("doi"))
    doi2 = normalize_doi(item2.get("doi"))
    if doi1 and doi2 and doi1 == doi2:
        return True

    # Match by PMID
    pmid1 = normalize_pmid(item1.get("pmid"))
    pmid2 = normalize_pmid(item2.get("pmid"))
    if pmid1 and pmid2 and pmid1 == pmid2:
        return True

    # Match by normalized title
    title1 = normalize_title(item1.get("title", ""))
    title2 = normalize_title(item2.get("title", ""))
    if title1 and title2 and len(title1) > 20 and len(title2) > 20:
        # Require substantial title length for matching
        if title1 == title2:
            return True
        # Check for very similar titles (Jaccard similarity on words)
        words1 = set(title1.split())
        words2 = set(title2.split())
        if words1 and words2:
            intersection = len(words1 & words2)
            union = len(words1 | words2)
            if union > 0 and intersection / union > 0.85:
                return True

    return False


def merge_datasets(
    *datasets: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge multiple datasets, combining labels for matching publications.

    Args:
        *datasets: Variable number of dataset lists

    Returns:
        Merged list with combined labels
    """
    merged: List[Dict[str, Any]] = []

    for dataset in datasets:
        for item in dataset:
            # Find existing match
            matched = False
            for existing in merged:
                if match_publications(item, existing):
                    # Merge labels
                    existing["human_labels"].extend(item.get("human_labels", []))
                    # Update missing fields
                    if not existing.get("title") and item.get("title"):
                        existing["title"] = item["title"]
                    if not existing.get("doi") and item.get("doi"):
                        existing["doi"] = item["doi"]
                    if not existing.get("pmid") and item.get("pmid"):
                        existing["pmid"] = item["pmid"]
                    if not existing.get("url") and item.get("url"):
                        existing["url"] = item["url"]
                    if not existing.get("abstract") and item.get("abstract"):
                        existing["abstract"] = item["abstract"]
                    matched = True
                    break

            if not matched:
                # Add as new item
                merged.append(item.copy())

    logger.info(f"Merged {sum(len(d) for d in datasets)} items into {len(merged)} unique publications")
    return merged


def compute_mean_human_rating(item: Dict[str, Any]) -> Optional[float]:
    """Compute mean human rating for an item.

    Args:
        item: Canonical schema item

    Returns:
        Mean rating (0-3 scale) or None if no labels
    """
    labels = item.get("human_labels", [])
    ratings = [l["rating_0_3"] for l in labels if l.get("rating_0_3") is not None]

    if not ratings:
        return None

    return sum(ratings) / len(ratings)


def get_udi_rating(item: Dict[str, Any]) -> Optional[int]:
    """Get Udi's rating for an item (ground truth).

    Args:
        item: Canonical schema item

    Returns:
        Udi's rating (0-3) or None if not present
    """
    labels = item.get("human_labels", [])
    for label in labels:
        if label.get("source") == "udi":
            return label.get("rating_0_3")
    return None


def filter_items_with_ratings(
    items: List[Dict[str, Any]],
    min_ratings: int = 1,
) -> List[Dict[str, Any]]:
    """Filter items to those with sufficient human ratings.

    Args:
        items: List of canonical items
        min_ratings: Minimum number of ratings required

    Returns:
        Filtered list
    """
    filtered = []
    for item in items:
        labels = item.get("human_labels", [])
        valid_ratings = [l for l in labels if l.get("rating_0_3") is not None]
        if len(valid_ratings) >= min_ratings:
            filtered.append(item)

    logger.info(f"Filtered {len(items)} items to {len(filtered)} with >= {min_ratings} ratings")
    return filtered


def load_tri_model_results_from_db(
    run_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    db_path: str = "data/db/acitrack.db",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load tri-model scoring results from database.

    Args:
        run_id: Optional run_id to filter by (exact match)
        experiment_id: Optional experiment_id to filter by (matches run_id=benchmark-{experiment_id})
        db_path: Path to SQLite database
        limit: Optional limit on number of results

    Returns:
        List of tri-model results with publication info
    """
    import sqlite3
    from pathlib import Path

    db_file = Path(db_path)
    if not db_file.exists():
        logger.warning(f"Database not found: {db_path}")
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Be schema-tolerant across older/newer publications table variants.
        cursor.execute("PRAGMA table_info(publications)")
        pub_columns = {row[1] for row in cursor.fetchall()}
        abstract_expr = "NULL as abstract"
        if "raw_text" in pub_columns:
            abstract_expr = "p.raw_text as abstract"
        elif "abstract" in pub_columns:
            abstract_expr = "p.abstract as abstract"
        elif "summary" in pub_columns:
            abstract_expr = "p.summary as abstract"

        url_expr = "NULL as url"
        if "url" in pub_columns:
            url_expr = "p.url as url"

        canonical_expr = "NULL as canonical_url"
        if "canonical_url" in pub_columns:
            canonical_expr = "p.canonical_url as canonical_url"
        elif "url" in pub_columns:
            canonical_expr = "p.url as canonical_url"

        doi_expr = "NULL as doi"
        if "doi" in pub_columns:
            doi_expr = "p.doi as doi"

        pmid_expr = "NULL as pmid"
        if "pmid" in pub_columns:
            pmid_expr = "p.pmid as pmid"

        # Build query
        query = """
            SELECT
                t.publication_id,
                t.title,
                t.source,
                t.run_id,
                t.final_relevancy_score,
                t.final_relevancy_reason,
                t.final_signals_json,
                t.final_summary,
                t.agreement_level,
                t.confidence,
                t.claude_review_json,
                t.gemini_review_json,
                t.gpt_eval_json,
                t.credibility_score,
                t.credibility_reason,
                {abstract_expr},
                {url_expr},
                {canonical_expr},
                {doi_expr},
                {pmid_expr}
            FROM tri_model_scoring_events t
            LEFT JOIN publications p ON t.publication_id = p.id
        """.format(
            abstract_expr=abstract_expr,
            url_expr=url_expr,
            canonical_expr=canonical_expr,
            doi_expr=doi_expr,
            pmid_expr=pmid_expr,
        )

        params = []
        conditions = []

        if run_id:
            conditions.append("t.run_id = ?")
            params.append(run_id)
        elif experiment_id:
            # Benchmark runs use run_id = "benchmark-{experiment_id}"
            conditions.append("t.run_id = ?")
            params.append(f"benchmark-{experiment_id}")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY t.created_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            result = dict(row)

            # Parse JSON fields
            for json_field in ["final_signals_json", "claude_review_json", "gemini_review_json", "gpt_eval_json"]:
                if result.get(json_field):
                    try:
                        result[json_field.replace("_json", "")] = json.loads(result[json_field])
                    except json.JSONDecodeError:
                        result[json_field.replace("_json", "")] = None

            results.append(result)

        conn.close()
        logger.info(f"Loaded {len(results)} tri-model results from database")
        return results

    except Exception as e:
        logger.error(f"Error loading tri-model results: {e}")
        return []


def enrich_items_with_tri_model(
    items: List[Dict[str, Any]],
    tri_model_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Enrich evaluation items with tri-model scoring results.

    Args:
        items: Canonical evaluation items
        tri_model_results: Tri-model results from database

    Returns:
        Items enriched with model_score and model_* fields
    """
    # Build lookup index by various identifiers
    tri_by_id: Dict[str, Dict] = {}
    tri_by_doi: Dict[str, Dict] = {}
    tri_by_pmid: Dict[str, Dict] = {}
    tri_by_canonical_url: Dict[str, Dict] = {}
    tri_by_title: Dict[str, Dict] = {}
    tri_titles: List[Tuple[str, Dict[str, Any]]] = []

    for result in tri_model_results:
        pub_id = result.get("publication_id")
        if pub_id:
            tri_by_id[pub_id] = result

        doi = normalize_doi(result.get("doi"))
        if doi:
            tri_by_doi[doi] = result

        pmid = normalize_pmid(result.get("pmid"))
        if pmid:
            tri_by_pmid[pmid] = result

        canonical_url = normalize_canonical_url(result.get("canonical_url") or result.get("url"))
        if canonical_url:
            tri_by_canonical_url[canonical_url] = result

        title = result.get("title")
        if title:
            title_key = normalize_title(title)
            tri_by_title[title_key] = result
            tri_titles.append((title_key, result))

    enriched = []
    matched_count = 0
    match_key_counts = {"publication_id": 0, "doi": 0, "pmid": 0, "canonical_url": 0, "title": 0}
    closest_title_matches: List[Tuple[float, str, str, Optional[str]]] = []

    for item in items:
        enriched_item = item.copy()

        # Try to find matching tri-model result
        result = None
        match_key = None

        # Match by publication_id
        pub_id = item.get("publication_id")
        if pub_id and pub_id in tri_by_id:
            result = tri_by_id[pub_id]
            match_key = "publication_id"

        # Match by DOI
        if not result:
            doi = normalize_doi(item.get("doi"))
            if doi and doi in tri_by_doi:
                result = tri_by_doi[doi]
                match_key = "doi"

        # Match by PMID
        if not result:
            pmid = normalize_pmid(item.get("pmid"))
            if pmid and pmid in tri_by_pmid:
                result = tri_by_pmid[pmid]
                match_key = "pmid"

        # Match by normalized title
        if not result:
            canonical_url = normalize_canonical_url(item.get("canonical_url") or item.get("url"))
            if canonical_url and canonical_url in tri_by_canonical_url:
                result = tri_by_canonical_url[canonical_url]
                match_key = "canonical_url"

        # Match by normalized title
        if not result:
            title = item.get("title")
            if title:
                title_key = normalize_title(title)
                if title_key in tri_by_title:
                    result = tri_by_title[title_key]
                    match_key = "title"

        if result:
            matched_count += 1
            if match_key:
                match_key_counts[match_key] += 1
            enriched_item["model_score"] = result.get("final_relevancy_score")
            enriched_item["model_reason"] = result.get("final_relevancy_reason")
            enriched_item["model_signals"] = result.get("final_signals")
            enriched_item["model_confidence"] = result.get("confidence")
            enriched_item["model_agreement_level"] = result.get("agreement_level")
            enriched_item["model_summary"] = result.get("final_summary")
            enriched_item["claude_review"] = result.get("claude_review")
            enriched_item["gemini_review"] = result.get("gemini_review")
            enriched_item["gpt_eval"] = result.get("gpt_eval")
            enriched_item["credibility_score"] = result.get("credibility_score")
            enriched_item["credibility_reason"] = result.get("credibility_reason")
            enriched_item["tri_model_run_id"] = result.get("run_id")
            enriched_item["source_type"] = "tri_model"
        else:
            enriched_item["model_score"] = None
            enriched_item["source_type"] = "no_match"
            title = item.get("title") or ""
            title_key = normalize_title(title)
            if title_key and tri_titles:
                from difflib import SequenceMatcher
                scored = []
                for candidate_key, candidate in tri_titles:
                    ratio = SequenceMatcher(None, title_key, candidate_key).ratio()
                    scored.append((ratio, candidate.get("title", ""), candidate.get("publication_id")))
                scored.sort(key=lambda x: x[0], reverse=True)
                for ratio, cand_title, cand_id in scored[:10]:
                    closest_title_matches.append((ratio, title, cand_title, cand_id))

        enriched.append(enriched_item)

    logger.info(f"Enriched {matched_count}/{len(items)} items with tri-model results")
    logger.info(
        "Match key usage: publication_id=%d, doi=%d, pmid=%d, canonical_url=%d, title=%d",
        match_key_counts["publication_id"],
        match_key_counts["doi"],
        match_key_counts["pmid"],
        match_key_counts["canonical_url"],
        match_key_counts["title"],
    )
    if closest_title_matches:
        closest_title_matches.sort(key=lambda x: x[0], reverse=True)
        logger.info("Top 10 closest title matches when no exact match found:")
        for ratio, query_title, cand_title, cand_id in closest_title_matches[:10]:
            logger.info("  %.3f | %s -> %s (publication_id=%s)", ratio, query_title[:80], cand_title[:80], cand_id)
    return enriched
