"""Google Drive upload integration for AciTrack outputs."""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

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
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

# MIME type mapping
MIME_TYPES = {
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}


def get_drive_service():
    """Create and return Google Drive API service instance.

    Returns:
        Google Drive API service object

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
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        service = build("drive", "v3", credentials=credentials)

        # Log service account email for debugging
        service_account_email = credentials.service_account_email
        logger.info("Using service account: %s", service_account_email)
        print(f"📧 Service account: {service_account_email}")

        return service
    except Exception as e:
        raise Exception(f"Failed to create Drive service: {e}")


def find_file_in_folder(service, folder_id: str, filename: str) -> Optional[str]:
    """Find a file by name in a specific folder.

    Args:
        service: Google Drive API service
        folder_id: Parent folder ID
        filename: Name of file to search for

    Returns:
        File ID if found, None otherwise
    """
    query = f"'{folder_id}' in parents and name='{filename}' and trashed=false"

    try:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()

        files = response.get("files", [])
        if files:
            logger.debug("Found existing file '%s': %s", filename, files[0]["id"])
            return files[0]["id"]
        return None
    except Exception as e:
        logger.error("Error searching for file '%s': %s", filename, e)
        return None


def upload_or_update_file(
    service,
    folder_id: str,
    local_path: Path,
    filename: str,
) -> dict:
    """Upload a new file or update existing file in Drive folder.

    Args:
        service: Google Drive API service
        folder_id: Target folder ID
        local_path: Path to local file
        filename: Desired filename in Drive

    Returns:
        Dictionary with file_id and webViewLink, or error info
    """
    # Determine MIME type
    mime_type = MIME_TYPES.get(local_path.suffix, "application/octet-stream")

    # Check if file already exists
    existing_file_id = find_file_in_folder(service, folder_id, filename)

    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)

    try:
        if existing_file_id:
            # Update existing file
            logger.info("Updating existing file: %s", filename)
            file = service.files().update(
                fileId=existing_file_id,
                media_body=media,
                supportsAllDrives=True,
                fields="id, webViewLink",
            ).execute()
            logger.info("✅ Updated: %s", filename)
        else:
            # Create new file
            logger.info("Creating new file: %s", filename)
            file_metadata = {
                "name": filename,
                "parents": [folder_id],
            }
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                supportsAllDrives=True,
                fields="id, webViewLink",
            ).execute()
            logger.info("✅ Created: %s", filename)

        return {
            "success": True,
            "file_id": file.get("id"),
            "webViewLink": file.get("webViewLink"),
        }
    except Exception as e:
        logger.error("❌ Failed to upload %s: %s", filename, e)
        return {
            "success": False,
            "error": str(e),
        }


def upload_latest_outputs(folder_id: str, outdir: str = "data") -> dict:
    """Upload the three latest output files to Google Drive.

    Args:
        folder_id: Google Drive folder ID (from ACITRACK_DRIVE_FOLDER_ID env var)
        outdir: Output directory containing the files (default: "data")

    Returns:
        Dictionary mapping filename to upload result (file_id, webViewLink, or error)
    """
    output_dir = Path(outdir) / "output"

    # Files to upload
    files_to_upload = [
        ("latest_report.md", output_dir / "latest_report.md"),
        ("latest_new.csv", output_dir / "latest_new.csv"),
        ("latest_manifest.json", output_dir / "latest_manifest.json"),
    ]

    # Create Drive service
    try:
        service = get_drive_service()
    except Exception as e:
        logger.error("Failed to create Drive service: %s", e)
        return {"error": str(e)}

    results = {}
    upload_failures = []

    print("\n" + "=" * 70)
    print("Uploading to Google Drive")
    print("=" * 70)
    print(f"Folder ID: {folder_id}")
    print()

    # Upload each file
    for filename, local_path in files_to_upload:
        if not local_path.exists():
            logger.warning("⚠️  File not found, skipping: %s", filename)
            results[filename] = {"success": False, "error": "File not found"}
            upload_failures.append(filename)
            continue

        result = upload_or_update_file(service, folder_id, local_path, filename)
        results[filename] = result

        if result.get("success"):
            print(f"✅ {filename}")
            if result.get("webViewLink"):
                print(f"   {result['webViewLink']}")
        else:
            print(f"❌ {filename}")
            print(f"   Error: {result.get('error')}")
            upload_failures.append(filename)

    print("=" * 70)

    if upload_failures:
        print(f"\n⚠️  {len(upload_failures)} file(s) failed to upload:")
        for filename in upload_failures:
            print(f"  - {filename}")
        results["_has_failures"] = True
    else:
        print("\n✅ All files uploaded successfully!")
        results["_has_failures"] = False

    return results
