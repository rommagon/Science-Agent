#!/usr/bin/env python3
"""Ingest tri-model daily run outputs into Postgres-backed backend.

This script uploads manifest.json, must_reads.json, and tri_model_events.jsonl
to the Render backend API.

Backend endpoints:
- POST /ingest/run (manifest.json as-is)
- POST /ingest/must-reads (wrapper with mode + run_id)
- POST /ingest/tri-model-events (bulk upsert with wrapper)

Usage:
    export BACKEND_URL="https://acitracker-backend-tri.onrender.com"
    export BACKEND_API_KEY="your-api-key"

    python scripts/ingest_to_backend.py --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12

    # Dry-run mode (validate payloads without POSTing)
    python scripts/ingest_to_backend.py --outdir data/outputs/tri-model-daily/tri-model-daily-2026-01-12 --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_json_file(file_path: Path) -> Dict:
    """Load and parse JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: If file does not exist
        json.JSONDecodeError: If file is not valid JSON
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl_file(file_path: Path) -> List[Dict]:
    """Load and parse JSONL file (streaming, memory-efficient).

    Args:
        file_path: Path to JSONL file

    Returns:
        List of parsed JSON objects

    Raises:
        FileNotFoundError: If file does not exist
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    events = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping invalid JSON at line {line_num}: {e}")
                continue

    return events


def post_with_retry(
    url: str,
    headers: Dict,
    json_data: Dict,
    timeout: int,
    retries: int,
    dry_run: bool = False,
) -> Dict:
    """POST JSON data with exponential backoff retry logic.

    Args:
        url: Target URL
        headers: HTTP headers
        json_data: JSON payload
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        dry_run: If True, skip actual POST

    Returns:
        Response dict with success, status_code, and response_data
    """
    if dry_run:
        logger.info(f"DRY-RUN: Would POST to {url}")
        logger.debug(f"DRY-RUN: Payload keys: {list(json_data.keys())}")
        return {
            "success": True,
            "status_code": 200,
            "response_data": {"dry_run": True},
        }

    for attempt in range(retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=json_data,
                timeout=timeout,
            )

            # Success
            if response.status_code in (200, 201):
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "response_data": response.json() if response.content else {},
                }

            # Client error (4xx) - don't retry
            if 400 <= response.status_code < 500:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": f"Client error: {response.text[:500]}",
                }

            # Server error (5xx) - retry with backoff
            if response.status_code >= 500:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"Server error {response.status_code}, retrying in {wait_time}s "
                    f"(attempt {attempt + 1}/{retries})"
                )
                if attempt < retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": f"Server error after {retries} retries: {response.text[:500]}",
                    }

        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout, retrying (attempt {attempt + 1}/{retries})")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            else:
                return {
                    "success": False,
                    "status_code": 0,
                    "error": f"Request timeout after {retries} retries",
                }

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "status_code": 0,
                "error": f"Request exception: {str(e)}",
            }

    return {
        "success": False,
        "status_code": 0,
        "error": "Unexpected retry loop exit",
    }


def ingest_manifest(
    backend_url: str,
    api_key: str,
    manifest_data: Dict,
    timeout: int,
    retries: int,
    dry_run: bool = False,
) -> Dict:
    """Ingest manifest to /ingest/run endpoint.

    Args:
        backend_url: Backend base URL
        api_key: API key for authentication
        manifest_data: Manifest JSON data
        timeout: Request timeout
        retries: Number of retries
        dry_run: If True, skip actual POST

    Returns:
        Result dict with success status
    """
    url = f"{backend_url}/ingest/run"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    logger.info("Ingesting manifest to /ingest/run")
    result = post_with_retry(url, headers, manifest_data, timeout, retries, dry_run)

    if result["success"]:
        logger.info(f"✓ Manifest ingestion successful (status={result['status_code']})")
    else:
        logger.error(f"✗ Manifest ingestion failed: {result.get('error')}")

    return result


def ingest_must_reads(
    backend_url: str,
    api_key: str,
    run_id: str,
    mode: str,
    must_reads_data: Dict,
    timeout: int,
    retries: int,
    dry_run: bool = False,
) -> Dict:
    """Ingest must-reads to /ingest/must-reads endpoint.

    Args:
        backend_url: Backend base URL
        api_key: API key for authentication
        run_id: Run identifier
        mode: Run mode (tri-model-daily)
        must_reads_data: Must-reads JSON data
        timeout: Request timeout
        retries: Number of retries
        dry_run: If True, skip actual POST

    Returns:
        Result dict with success status
    """
    url = f"{backend_url}/ingest/must-reads"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    # Wrap must-reads data
    payload = {
        "run_id": run_id,
        "mode": mode,
        "must_reads": must_reads_data,
    }

    logger.info("Ingesting must-reads to /ingest/must-reads")
    result = post_with_retry(url, headers, payload, timeout, retries, dry_run)

    if result["success"]:
        logger.info(f"✓ Must-reads ingestion successful (status={result['status_code']})")
    else:
        logger.error(f"✗ Must-reads ingestion failed: {result.get('error')}")

    return result


def ingest_tri_model_events(
    backend_url: str,
    api_key: str,
    run_id: str,
    mode: str,
    events: List[Dict],
    chunk_size: int,
    timeout: int,
    retries: int,
    dry_run: bool = False,
) -> Dict:
    """Ingest tri-model events to /ingest/tri-model-events endpoint in chunks.

    Args:
        backend_url: Backend base URL
        api_key: API key for authentication
        run_id: Run identifier
        mode: Run mode (tri-model-daily)
        events: List of event dicts
        chunk_size: Batch size for chunked uploads
        timeout: Request timeout
        retries: Number of retries
        dry_run: If True, skip actual POST

    Returns:
        Result dict with success status and stats
    """
    url = f"{backend_url}/ingest/tri-model-events"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    total_events = len(events)
    total_chunks = (total_events + chunk_size - 1) // chunk_size

    logger.info(f"Ingesting {total_events} events in {total_chunks} chunks (chunk_size={chunk_size})")

    successful_requests = 0
    failed_requests = 0

    for i in range(0, total_events, chunk_size):
        chunk = events[i:i + chunk_size]
        chunk_num = (i // chunk_size) + 1

        payload = {
            "run_id": run_id,
            "mode": mode,
            "events": chunk,
        }

        logger.info(f"Uploading chunk {chunk_num}/{total_chunks} ({len(chunk)} events)")
        result = post_with_retry(url, headers, payload, timeout, retries, dry_run)

        if result["success"]:
            successful_requests += 1
        else:
            failed_requests += 1
            logger.error(f"Chunk {chunk_num} failed: {result.get('error')}")

    success = failed_requests == 0

    if success:
        logger.info(f"✓ Tri-model events ingestion successful ({total_events} events, {total_chunks} requests)")
    else:
        logger.error(f"✗ Tri-model events ingestion failed ({failed_requests}/{total_chunks} requests failed)")

    return {
        "success": success,
        "total_events": total_events,
        "total_chunks": total_chunks,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
    }


def validate_outdir(outdir: Path) -> None:
    """Validate that output directory contains required files.

    Args:
        outdir: Output directory path

    Raises:
        FileNotFoundError: If required files are missing
    """
    required_files = ["manifest.json", "must_reads.json", "tri_model_events.jsonl"]

    for file_name in required_files:
        file_path = outdir / file_name
        if not file_path.exists():
            raise FileNotFoundError(
                f"Required file not found: {file_path}\n"
                f"Expected tri-model run output directory with manifest.json, must_reads.json, and tri_model_events.jsonl"
            )


def main() -> int:
    """Main entrypoint for backend ingestion script."""
    parser = argparse.ArgumentParser(
        description="Ingest tri-model daily run outputs to Postgres-backed backend",
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        help="Backend base URL (default: from BACKEND_URL env var)",
    )
    parser.add_argument(
        "--backend-api-key",
        type=str,
        help="Backend API key (default: from BACKEND_API_KEY env var)",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        required=True,
        help="Path to run output directory (e.g., data/outputs/tri-model-daily/tri-model-daily-2026-01-12)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="tri-model-daily",
        help="Run mode (default: tri-model-daily)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        help="Run identifier (default: from manifest.json)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="Batch size for tri-model events (default: 100)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Request timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retry attempts (default: 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate payloads without POSTing to backend",
    )

    args = parser.parse_args()

    # Get backend URL
    backend_url = args.backend_url or os.getenv("BACKEND_URL")
    if not backend_url:
        logger.error("Backend URL not provided. Use --backend-url or set BACKEND_URL env var.")
        return 1

    # Remove trailing slash
    backend_url = backend_url.rstrip("/")

    # Get API key
    api_key = args.backend_api_key or os.getenv("BACKEND_API_KEY")
    if not api_key:
        logger.error("Backend API key not provided. Use --backend-api-key or set BACKEND_API_KEY env var.")
        return 1

    # Validate output directory
    outdir = Path(args.outdir)
    if not outdir.exists():
        logger.error(f"Output directory does not exist: {outdir}")
        return 1

    try:
        validate_outdir(outdir)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    # Load manifest
    manifest_path = outdir / "manifest.json"
    try:
        manifest_data = load_json_file(manifest_path)
    except Exception as e:
        logger.error(f"Failed to load manifest: {e}")
        return 1

    # Get run_id (from args or manifest)
    run_id = args.run_id or manifest_data.get("run_id")
    if not run_id:
        logger.error("Run ID not found in manifest and not provided via --run-id")
        return 1

    # Load must-reads
    must_reads_path = outdir / "must_reads.json"
    try:
        must_reads_data = load_json_file(must_reads_path)
    except Exception as e:
        logger.error(f"Failed to load must-reads: {e}")
        return 1

    # Load tri-model events
    events_path = outdir / "tri_model_events.jsonl"
    try:
        events = load_jsonl_file(events_path)
    except Exception as e:
        logger.error(f"Failed to load tri-model events: {e}")
        return 1

    # Print ingestion plan
    print("\n" + "=" * 70)
    print("Backend Ingestion Plan")
    print("=" * 70)
    print(f"Backend URL:     {backend_url}")
    print(f"Run ID:          {run_id}")
    print(f"Mode:            {args.mode}")
    print(f"Output dir:      {outdir}")
    print(f"Events count:    {len(events)}")
    print(f"Chunk size:      {args.chunk_size}")
    print(f"Timeout:         {args.timeout}s")
    print(f"Retries:         {args.retries}")
    if args.dry_run:
        print(f"DRY-RUN MODE:    Enabled (no actual POSTs)")
    print("=" * 70 + "\n")

    # Phase 1: Ingest manifest
    manifest_result = ingest_manifest(
        backend_url=backend_url,
        api_key=api_key,
        manifest_data=manifest_data,
        timeout=args.timeout,
        retries=args.retries,
        dry_run=args.dry_run,
    )

    if not manifest_result["success"]:
        logger.error("Manifest ingestion failed, aborting")
        return 1

    # Phase 2: Ingest must-reads
    must_reads_result = ingest_must_reads(
        backend_url=backend_url,
        api_key=api_key,
        run_id=run_id,
        mode=args.mode,
        must_reads_data=must_reads_data,
        timeout=args.timeout,
        retries=args.retries,
        dry_run=args.dry_run,
    )

    if not must_reads_result["success"]:
        logger.error("Must-reads ingestion failed, aborting")
        return 1

    # Phase 3: Ingest tri-model events
    events_result = ingest_tri_model_events(
        backend_url=backend_url,
        api_key=api_key,
        run_id=run_id,
        mode=args.mode,
        events=events,
        chunk_size=args.chunk_size,
        timeout=args.timeout,
        retries=args.retries,
        dry_run=args.dry_run,
    )

    if not events_result["success"]:
        logger.error("Tri-model events ingestion failed")
        return 1

    # Final summary
    print("\n" + "=" * 70)
    print("BACKEND INGESTION COMPLETE")
    print("=" * 70)
    print(f"✓ Manifest:      Ingested (run_id={run_id})")
    print(f"✓ Must-reads:    Ingested")
    print(f"✓ Tri-events:    Ingested ({events_result['total_events']} events, {events_result['total_chunks']} requests)")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
