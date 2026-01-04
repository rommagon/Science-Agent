"""Google Sheets integration for AciTrack.

Handles upserting publications to existing Master_Publications sheet (24 columns)
and updating System_Health metrics in key-value format.

IMPORTANT: This module works with an EXISTING Google Sheet with a fixed schema.
It NEVER modifies headers or creates new sheets.

Uses batch writes to avoid API quota limits (60 writes/min).
"""

import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict

# Fix for Python 3.9: google-api-core tries to use importlib.metadata.packages_distributions()
# which doesn't exist in Python 3.9. We use the importlib-metadata backport instead.
try:
    import importlib_metadata
    # Replace the standard library's importlib.metadata with the backport
    sys.modules['importlib.metadata'] = importlib_metadata
except ImportError:
    # Python 3.10+ doesn't need the backport
    pass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Canonical Master_Publications schema (24 columns, exact order)
MASTER_PUBLICATIONS_HEADERS = [
    "run_id",
    "date_added",
    "is_new_this_run",
    "must_read",
    "overall_rank",
    "title",
    "publication_year",
    "journal_or_source",
    "publication_type",
    "url",
    "authors",
    "institutions",
    "country",
    "primary_modality",
    "cancer_type",
    "sample_type",
    "tags",
    "sample_size",
    "reported_performance",
    "corporate_ties",
    "notes",
    "source_ingestion_method",
    "source_query",
    "record_id",
]


def _retry_with_backoff(func, max_retries=6, base_delay=1.0):
    """Execute function with exponential backoff retry on 429/503 errors.

    Args:
        func: Callable to execute (should return result or raise HttpError)
        max_retries: Maximum number of retry attempts (default: 6)
        base_delay: Base delay in seconds for exponential backoff (default: 1.0)

    Returns:
        Result from func()

    Raises:
        HttpError: If all retries exhausted or non-retryable error
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except HttpError as e:
            # Only retry on 429 (quota) or 503 (service unavailable)
            if e.resp.status not in (429, 503):
                raise

            if attempt >= max_retries:
                logger.error("Max retries (%d) exhausted for API call", max_retries)
                raise

            # Exponential backoff with jitter
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(
                "API quota/throttle error (HTTP %d), retrying in %.2fs (attempt %d/%d)",
                e.resp.status, delay, attempt + 1, max_retries
            )
            time.sleep(delay)

    # Should never reach here
    raise RuntimeError("Retry logic failed unexpectedly")


def get_sheets_service():
    """Create and return Google Sheets API service instance.

    Returns:
        Google Sheets API service object

    Raises:
        ValueError: If GOOGLE_APPLICATION_CREDENTIALS env var is not set
        Exception: If credentials file is invalid or service creation fails
    """
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        raise ValueError(
            "GOOGLE_APPLICATION_CREDENTIALS environment variable not set. "
            "Please set it to the path of your service account JSON key file."
        )

    if not os.path.exists(creds_path):
        raise ValueError(
            f"Credentials file not found at: {creds_path}"
        )

    try:
        credentials = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly"
            ]
        )
        service = build("sheets", "v4", credentials=credentials)

        # Log service account email for debugging
        service_account_email = credentials.service_account_email
        logger.info("Using service account for Sheets: %s", service_account_email)

        return service
    except Exception as e:
        raise Exception(f"Failed to create Sheets service: {e}")


def validate_sheet_headers(
    spreadsheet_id: str,
    sheet_name: str = "Master_Publications"
) -> bool:
    """Validate that the sheet headers match the canonical 24-column schema.

    This function ONLY validates - it NEVER modifies headers.

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID
        sheet_name: Name of the sheet tab (default: Master_Publications)

    Returns:
        True if headers match exactly

    Raises:
        ValueError: If headers don't match the canonical schema
    """
    try:
        service = get_sheets_service()

        # Get header row
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1:X1"  # A-X = 24 columns
        ).execute()

        headers = result.get("values", [[]])[0]

        # Validate exact match
        if headers != MASTER_PUBLICATIONS_HEADERS:
            # Build detailed error message
            expected_str = ", ".join(MASTER_PUBLICATIONS_HEADERS)
            actual_str = ", ".join(headers) if headers else "(empty)"

            error_msg = (
                f"Sheet '{sheet_name}' header mismatch!\n"
                f"Expected (24 columns): {expected_str}\n"
                f"Actual ({len(headers)} columns): {actual_str}\n\n"
                f"CRITICAL: The sheet schema must match exactly. "
                f"Do NOT modify the existing sheet headers."
            )
            raise ValueError(error_msg)

        logger.info("Sheet '%s' headers validated successfully (24 columns)", sheet_name)
        return True

    except Exception as e:
        logger.error("Failed to validate sheet headers: %s", e)
        raise


def _extract_publication_year(pub: dict) -> str:
    """Extract publication year from date field.

    Args:
        pub: Publication dictionary

    Returns:
        Year as string (YYYY) or empty string
    """
    date_str = pub.get("date", "")
    if date_str and len(date_str) >= 4:
        return date_str[:4]  # Extract YYYY from ISO date
    return ""


def _map_publication_to_row(
    pub: dict,
    run_id: str,
    is_new: bool,
    date_added: Optional[str] = None
) -> List[str]:
    """Map publication dict to 24-column row matching canonical schema.

    Args:
        pub: Publication dictionary from pipeline
        run_id: Current run identifier (YYYY-MM-DD)
        is_new: Whether this is a new publication this run
        date_added: Existing date_added value (for updates) or None (for new rows)

    Returns:
        List of 24 values in exact header order
    """
    # Format authors (handle list or string)
    authors = pub.get("authors", [])
    if isinstance(authors, list):
        authors_str = "; ".join(authors) if authors else ""
    else:
        authors_str = str(authors) if authors else ""

    # Get current UTC timestamp for new rows
    now_utc = datetime.now(timezone.utc).isoformat()

    # Determine date_added: use from dict if provided, else use existing for updates, else now for new
    if "date_added" in pub and pub["date_added"]:
        final_date_added = pub["date_added"]
    elif date_added:
        final_date_added = date_added
    else:
        final_date_added = now_utc

    # Map to 24-column schema
    row = [
        run_id,                                      # run_id
        final_date_added,                            # date_added (preserve on update)
        is_new,                                      # is_new_this_run (boolean True/False)
        pub.get("must_read", ""),                    # must_read (blank unless provided)
        "",                                          # overall_rank (leave blank)
        pub.get("title", ""),                        # title
        _extract_publication_year(pub),              # publication_year
        pub.get("venue", "") or pub.get("journal", ""),  # journal_or_source (prefer venue)
        "",                                          # publication_type (leave blank)
        pub.get("url", ""),                          # url
        authors_str,                                 # authors
        "",                                          # institutions (leave blank)
        "",                                          # country (leave blank)
        "",                                          # primary_modality (leave blank)
        "",                                          # cancer_type (leave blank)
        "",                                          # sample_type (leave blank)
        "",                                          # tags (leave blank)
        "",                                          # sample_size (leave blank)
        "",                                          # reported_performance (leave blank)
        "",                                          # corporate_ties (leave blank)
        pub.get("summary", ""),                      # notes (use AI summary)
        pub.get("source", ""),                       # source_ingestion_method
        "",                                          # source_query (leave blank)
        pub.get("id", ""),                           # record_id
    ]

    return row


def upsert_publications(
    spreadsheet_id: str,
    publications: List[dict],
    run_id: str,
    sheet_name: str = "Master_Publications"
) -> dict:
    """Upsert publications to Google Sheet by record_id using batch writes.

    Inserts new publications or updates existing ones based on record_id (column X).
    For new rows: sets is_new_this_run=TRUE and date_added to current UTC.
    For existing rows: preserves date_added, sets is_new_this_run=FALSE.

    Uses batch API calls to avoid quota limits:
    - One batchUpdate for all updates
    - One append for all inserts
    - Exponential backoff retry on 429/503 errors

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID
        publications: List of publication dictionaries with fields:
            - id (record_id)
            - title
            - authors (list or string)
            - source
            - venue
            - date (published_date)
            - url
            - summary
        run_id: Current run identifier (YYYY-MM-DD for daily runs)
        sheet_name: Name of the sheet tab (default: Master_Publications)

    Returns:
        dict with stats: {
            "inserted": int,
            "updated": int,
            "total_processed": int,
            "errors": int
        }
    """
    if not publications:
        logger.info("No publications to upsert")
        return {"inserted": 0, "updated": 0, "total_processed": 0, "errors": 0}

    try:
        service = get_sheets_service()

        # Validate headers first (fail fast if schema mismatch)
        validate_sheet_headers(spreadsheet_id, sheet_name)

        # Fetch only required columns for scalability (B=date_added, X=record_id)
        # This is efficient even with thousands of rows
        batch_result = service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=[
                f"{sheet_name}!B2:B",  # date_added (skip header)
                f"{sheet_name}!X2:X"   # record_id (skip header)
            ]
        ).execute()

        value_ranges = batch_result.get("valueRanges", [])
        date_added_rows = value_ranges[0].get("values", []) if len(value_ranges) > 0 else []
        record_id_rows = value_ranges[1].get("values", []) if len(value_ranges) > 1 else []

        # Build index: record_id -> (row_number, date_added)
        existing_records = {}
        for idx in range(max(len(date_added_rows), len(record_id_rows))):
            row_number = idx + 2  # +2 because we start at row 2 (after header)

            # Get record_id (column X)
            record_id = record_id_rows[idx][0] if idx < len(record_id_rows) and record_id_rows[idx] else ""

            # Get date_added (column B)
            date_added = date_added_rows[idx][0] if idx < len(date_added_rows) and date_added_rows[idx] else ""

            if record_id:  # Only index rows with valid record_id
                existing_records[record_id] = (row_number, date_added)

        logger.info("Loaded %d existing records from sheet", len(existing_records))

        # Separate publications into updates and inserts
        updates_data = []  # List of {"range": "...", "values": [[...]]}
        inserts_rows = []  # List of row data to append
        inserted = 0
        updated = 0
        errors = 0

        for pub in publications:
            try:
                record_id = pub.get("id", "")
                if not record_id:
                    logger.warning("Skipping publication without id: %s", pub.get("title", "Unknown"))
                    errors += 1
                    continue

                if record_id in existing_records:
                    # Update existing row (preserve date_added, set is_new_this_run=FALSE)
                    row_number, existing_date_added = existing_records[record_id]
                    row = _map_publication_to_row(pub, run_id, is_new=False, date_added=existing_date_added)

                    updates_data.append({
                        "range": f"{sheet_name}!A{row_number}:X{row_number}",
                        "values": [row]
                    })
                    updated += 1
                    logger.debug("Prepared update for publication: %s (row %d)", record_id, row_number)
                else:
                    # Insert new row (set is_new_this_run=TRUE, date_added=now)
                    row = _map_publication_to_row(pub, run_id, is_new=True, date_added=None)
                    inserts_rows.append(row)
                    inserted += 1
                    logger.debug("Prepared insert for publication: %s", record_id)

            except Exception as e:
                logger.error("Error preparing publication %s: %s", pub.get("id", "Unknown"), e)
                errors += 1

        # Execute batch update (one API call for all updates)
        if updates_data:
            logger.info("Executing batch update for %d publications...", len(updates_data))

            def batch_update():
                return service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"data": updates_data, "valueInputOption": "RAW"}
                ).execute()

            _retry_with_backoff(batch_update)
            logger.info("Batch update completed: %d rows updated", len(updates_data))

        # Execute batch append (one API call for all inserts)
        if inserts_rows:
            logger.info("Executing batch append for %d publications...", len(inserts_rows))

            def batch_append():
                return service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet_name}!A:X",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": inserts_rows}
                ).execute()

            _retry_with_backoff(batch_append)
            logger.info("Batch append completed: %d rows inserted", len(inserts_rows))

        logger.info(
            "Publications upserted: %d inserted, %d updated, %d errors",
            inserted, updated, errors
        )

        return {
            "inserted": inserted,
            "updated": updated,
            "total_processed": len(publications),
            "errors": errors
        }

    except Exception as e:
        logger.error("Failed to upsert publications to Google Sheets: %s", e)
        raise


def verify_run_consistency(
    spreadsheet_id: str,
    run_id: str,
    csv_path: str,
    sheet_name: str = "Master_Publications"
) -> dict:
    """Verify CSV vs Sheets consistency for a specific run.

    Checks that all records in CSV appear in Sheets with matching run_id
    and is_new_this_run=TRUE.

    This is more accurate than verify_sheets_csv_consistency() because it's
    scoped to a specific run and checks the is_new_this_run flag.

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID
        run_id: Run identifier (YYYY-MM-DD)
        csv_path: Path to CSV file (latest_new.csv)
        sheet_name: Name of the sheet tab (default: Master_Publications)

    Returns:
        dict with check results: {
            "csv_count": int,
            "sheets_new_count": int,
            "missing_in_sheets": list[str],  # record_ids
            "all_present": bool
        }
    """
    import csv
    from pathlib import Path

    result = {
        "csv_count": 0,
        "sheets_new_count": 0,
        "missing_in_sheets": [],
        "all_present": False
    }

    try:
        # Read record_ids from CSV
        csv_file = Path(csv_path)
        if not csv_file.exists():
            logger.warning("CSV file not found: %s", csv_path)
            return result

        csv_record_ids = set()
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'id' in row and row['id']:
                    csv_record_ids.add(row['id'])

        result["csv_count"] = len(csv_record_ids)

        if not csv_record_ids:
            logger.info("CSV is empty or has no 'id' column")
            result["all_present"] = True
            return result

        # Fetch columns A (run_id), C (is_new_this_run), X (record_id) efficiently
        service = get_sheets_service()
        batch_result = service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=[
                f"{sheet_name}!A2:A",  # run_id
                f"{sheet_name}!C2:C",  # is_new_this_run
                f"{sheet_name}!X2:X"   # record_id
            ]
        ).execute()

        value_ranges = batch_result.get("valueRanges", [])
        run_id_rows = value_ranges[0].get("values", []) if len(value_ranges) > 0 else []
        is_new_rows = value_ranges[1].get("values", []) if len(value_ranges) > 1 else []
        record_id_rows = value_ranges[2].get("values", []) if len(value_ranges) > 2 else []

        # Filter for rows with matching run_id and is_new_this_run=TRUE
        sheets_new_record_ids = set()
        for idx in range(max(len(run_id_rows), len(is_new_rows), len(record_id_rows))):
            row_run_id = run_id_rows[idx][0] if idx < len(run_id_rows) and run_id_rows[idx] else ""
            is_new = is_new_rows[idx][0] if idx < len(is_new_rows) and is_new_rows[idx] else ""
            record_id = record_id_rows[idx][0] if idx < len(record_id_rows) and record_id_rows[idx] else ""

            # Match this run AND is_new=TRUE (or "TRUE" for backward compat)
            if row_run_id == run_id and is_new in (True, "TRUE", "true") and record_id:
                sheets_new_record_ids.add(record_id)

        result["sheets_new_count"] = len(sheets_new_record_ids)

        # Find missing records
        missing = csv_record_ids - sheets_new_record_ids
        result["missing_in_sheets"] = list(missing)
        result["all_present"] = len(missing) == 0

        if result["all_present"]:
            logger.info(
                "Run consistency check PASSED: All %d CSV records found in Sheets with run_id=%s and is_new=TRUE",
                result["csv_count"],
                run_id
            )
        else:
            logger.warning(
                "Run consistency check FAILED: %d/%d CSV records missing in Sheets (run_id=%s, is_new=TRUE)",
                len(missing),
                result["csv_count"],
                run_id
            )
            for record_id in list(missing)[:5]:  # Log first 5
                logger.warning("  Missing record_id: %s", record_id)

        return result

    except Exception as e:
        logger.error("Run consistency check failed: %s", e)
        result["error"] = str(e)
        return result


def verify_sheets_csv_consistency(
    spreadsheet_id: str,
    csv_path: str,
    sheet_name: str = "Master_Publications"
) -> dict:
    """Verify that all records in CSV exist in Google Sheets.

    Legacy function. For run-specific checks, use verify_run_consistency() instead.

    Best-effort consistency check between latest_new.csv and Master_Publications.

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID
        csv_path: Path to CSV file (latest_new.csv)
        sheet_name: Name of the sheet tab (default: Master_Publications)

    Returns:
        dict with check results: {
            "csv_count": int,
            "sheets_count": int,
            "missing_in_sheets": list[str],  # record_ids
            "all_present": bool
        }
    """
    import csv
    from pathlib import Path

    result = {
        "csv_count": 0,
        "sheets_count": 0,
        "missing_in_sheets": [],
        "all_present": False
    }

    try:
        # Read record_ids from CSV
        csv_file = Path(csv_path)
        if not csv_file.exists():
            logger.warning("CSV file not found: %s", csv_path)
            return result

        csv_record_ids = set()
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'id' in row and row['id']:
                    csv_record_ids.add(row['id'])

        result["csv_count"] = len(csv_record_ids)

        if not csv_record_ids:
            logger.info("CSV is empty or has no 'id' column")
            result["all_present"] = True
            return result

        # Read record_ids from Sheets (column X = record_id)
        service = get_sheets_service()
        sheets_result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!X:X"  # Column X = record_id
        ).execute()
        rows = sheets_result.get("values", [])

        sheets_record_ids = {row[0] for row in rows[1:] if row and row[0]}  # Skip header
        result["sheets_count"] = len(sheets_record_ids)

        # Find missing records
        missing = csv_record_ids - sheets_record_ids
        result["missing_in_sheets"] = list(missing)
        result["all_present"] = len(missing) == 0

        if result["all_present"]:
            logger.info(
                "Consistency check PASSED: All %d CSV records found in Sheets",
                result["csv_count"]
            )
        else:
            logger.warning(
                "Consistency check FAILED: %d/%d CSV records missing in Sheets",
                len(missing),
                result["csv_count"]
            )
            for record_id in list(missing)[:5]:  # Log first 5
                logger.warning("  Missing record_id: %s", record_id)

        return result

    except Exception as e:
        logger.error("Consistency check failed: %s", e)
        result["error"] = str(e)
        return result


def update_system_health(
    spreadsheet_id: str,
    run_id: str,
    total_publications_evaluated: int,
    new_this_run: int,
    must_reads_count: int = 0,
    publications_scanned_30d: int = 0,
    last_error: str = "",
    sheet_name: str = "System_Health"
) -> bool:
    """Update System_Health sheet with run metrics in key-value format using batch writes.

    System_Health is structured as:
    - Column A: Keys (last_run_at, publications_scanned_30d, total_publications_evaluated, etc.)
    - Column B: Values

    Uses a single batchUpdate call to update multiple cells efficiently.

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID
        run_id: Run identifier (YYYY-MM-DD for daily runs)
        total_publications_evaluated: Total publications processed
        new_this_run: Count of new publications in this run
        must_reads_count: Count of must-read publications (default: 0)
        publications_scanned_30d: Publications scanned in last 30 days (default: 0)
        last_error: Error message if run failed (default: "")
        sheet_name: Name of the sheet tab (default: System_Health)

    Returns:
        True if successful, False otherwise
    """
    try:
        service = get_sheets_service()

        # Prepare key-value pairs to update
        now_utc = datetime.now(timezone.utc).isoformat()

        updates = {
            "last_run_at": now_utc,
            "publications_scanned_30d": str(publications_scanned_30d),
            "total_publications_evaluated": str(total_publications_evaluated),
            "must_reads_count": str(must_reads_count),
            "new_this_run": str(new_this_run),
            "last_error": last_error  # Blank on success
        }

        # Get existing keys from column A
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:B"
        ).execute()
        rows = result.get("values", [])

        # Build index: key -> row_number
        key_index = {}
        for idx, row in enumerate(rows, start=1):
            if row and row[0]:  # If key exists in column A
                key_index[row[0]] = idx

        # Prepare batch update data
        updates_data = []  # For existing keys
        inserts_rows = []  # For new keys

        for key, value in updates.items():
            if key in key_index:
                # Update existing row - just update column B
                row_number = key_index[key]
                updates_data.append({
                    "range": f"{sheet_name}!B{row_number}",
                    "values": [[value]]
                })
                logger.debug("Prepared update for System_Health key '%s' = '%s'", key, value)
            else:
                # New key-value pair to append
                inserts_rows.append([key, value])
                logger.debug("Prepared insert for System_Health key '%s' = '%s'", key, value)

        # Execute batch update for existing keys (one API call)
        if updates_data:
            logger.info("Executing batch update for %d System_Health keys...", len(updates_data))

            def batch_update():
                return service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"data": updates_data, "valueInputOption": "RAW"}
                ).execute()

            _retry_with_backoff(batch_update)
            logger.info("System_Health batch update completed: %d keys updated", len(updates_data))

        # Execute batch append for new keys (one API call)
        if inserts_rows:
            logger.info("Executing batch append for %d new System_Health keys...", len(inserts_rows))

            def batch_append():
                return service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet_name}!A:B",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": inserts_rows}
                ).execute()

            _retry_with_backoff(batch_append)
            logger.info("System_Health batch append completed: %d keys inserted", len(inserts_rows))

        logger.info("System_Health updated successfully for run_id: %s", run_id)
        return True

    except Exception as e:
        logger.error("Failed to update System_Health: %s", e)
        return False
