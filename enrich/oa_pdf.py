"""Open-access PDF retrieval cascade.

Given a publication (DOI preferred, PMID/title as fallbacks), tries to
legally retrieve a PDF by querying open-access metadata providers in
order:

    1. Unpaywall          (aggregates OA locations across publishers/repos)
    2. Europe PMC         (NIH-funded biomedical full text)
    3. Crossref           (publisher-declared license + TDM links)
    4. bioRxiv / medRxiv  (preprints with predictable PDF URLs)

Each provider returns (pdf_bytes, license, source_api, source_url) on
success or None. The orchestrator short-circuits on the first hit.

The caller is responsible for the redistribution policy — this module
reports the license but does not decide whether the PDF can be attached
to an email. See `is_attachable_license()` for the canonical check.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# --- Constants ----------------------------------------------------------

USER_AGENT = "acitracker-oa-fetch/1.0 (+https://ai.spotitearly.com; mailto:{email})"
DEFAULT_TIMEOUT = 30
MIN_PDF_BYTES = 5 * 1024          # 5 KB — anything smaller is not a real paper
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB — defensive upper bound

# Licenses under which we consider it safe to attach a PDF to an internal
# distribution email. Conservative additions ('bronze', 'unknown') deliberately
# excluded — those we link to via Emory proxy instead.
ATTACHABLE_LICENSES = frozenset({
    "cc-by",
    "cc-by-sa",
    "cc0",
    "public-domain",
    "cc-by-nc",       # internal non-commercial use OK
    "cc-by-nc-sa",
    "cc-by-nc-nd",
})


# --- Result type --------------------------------------------------------

@dataclass
class OaPdfResult:
    """Successfully-fetched OA PDF plus provenance."""

    pdf_bytes: bytes
    license: str         # canonical form, see _normalize_license()
    source_api: str      # 'unpaywall' | 'europepmc' | 'crossref' | 'biorxiv' | 'medrxiv'
    source_url: str      # URL we downloaded from

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pdf_bytes).hexdigest()

    @property
    def attachable(self) -> bool:
        return is_attachable_license(self.license)


def is_attachable_license(license_str: Optional[str]) -> bool:
    """Is this license permissive enough to attach the PDF to an email?

    Conservative by default — unknown/missing licenses are NOT attachable.
    """
    if not license_str:
        return False
    return license_str.lower() in ATTACHABLE_LICENSES


# --- License normalization ---------------------------------------------

# Map common CC license URL fragments → canonical short form.
_LICENSE_URL_MAP = [
    ("creativecommons.org/publicdomain/zero", "cc0"),
    ("creativecommons.org/publicdomain/mark",  "public-domain"),
    ("creativecommons.org/licenses/by-nc-nd",  "cc-by-nc-nd"),
    ("creativecommons.org/licenses/by-nc-sa",  "cc-by-nc-sa"),
    ("creativecommons.org/licenses/by-nc",     "cc-by-nc"),
    ("creativecommons.org/licenses/by-sa",     "cc-by-sa"),
    ("creativecommons.org/licenses/by-nd",     "cc-by-nd"),
    ("creativecommons.org/licenses/by",        "cc-by"),
]


def _normalize_license(raw: Optional[str]) -> str:
    """Map a license string or URL to canonical short form.

    Unknown / empty → 'unknown' (not attachable).
    """
    if not raw:
        return "unknown"
    s = raw.strip().lower()
    # Short-form already?
    if s in ATTACHABLE_LICENSES or s in {"bronze", "closed", "unknown"}:
        return s
    # URL form
    for fragment, canonical in _LICENSE_URL_MAP:
        if fragment in s:
            return canonical
    if "publicdomain" in s or "public domain" in s:
        return "public-domain"
    return "unknown"


# --- HTTP helpers -------------------------------------------------------

def _session(email: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT.format(email=email)})
    return s


def _download_pdf(
    url: str,
    session: requests.Session,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[bytes]:
    """Download a URL and validate it looks like a PDF.

    Validates via magic bytes (%PDF) rather than Content-Type, which
    publishers often set incorrectly for OA PDFs.
    """
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
    except requests.RequestException as e:
        logger.debug("PDF download failed for %s: %s", url, e)
        return None

    if r.status_code != 200:
        logger.debug("PDF download non-200 for %s: %d", url, r.status_code)
        return None

    # Stream up to MAX_PDF_BYTES
    chunks = []
    total = 0
    for chunk in r.iter_content(chunk_size=65536):
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_PDF_BYTES:
            logger.debug("PDF exceeded max size for %s", url)
            return None
    body = b"".join(chunks)

    if len(body) < MIN_PDF_BYTES:
        logger.debug("PDF too small for %s: %d bytes", url, len(body))
        return None
    if not body.startswith(b"%PDF"):
        logger.debug("Not a PDF (bad magic bytes) for %s", url)
        return None

    return body


# --- PMID → DOI via NCBI ID converter -----------------------------------

def pmid_to_doi(
    pmid: str,
    session: requests.Session,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[Optional[str], Optional[str]]:
    """Convert a PMID to (DOI, PMCID) via NCBI's ID converter.

    Either element may be None.
    """
    url = (
        "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        f"?ids={pmid}&format=json"
    )
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        records = r.json().get("records", [])
        if not records:
            return None, None
        rec = records[0]
        return rec.get("doi"), rec.get("pmcid")
    except (requests.RequestException, ValueError) as e:
        logger.debug("PMID->DOI conversion failed for %s: %s", pmid, e)
        return None, None


# --- Provider: Unpaywall ------------------------------------------------

def _try_unpaywall(
    doi: str, email: str, session: requests.Session,
) -> Optional[OaPdfResult]:
    """Query Unpaywall, then iterate OA locations and try downloading."""
    api = f"https://api.unpaywall.org/v2/{quote(doi)}?email={quote(email)}"
    try:
        r = session.get(api, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.debug("Unpaywall lookup failed for %s: %s", doi, e)
        return None

    if not data.get("is_oa"):
        return None

    # Collect candidate locations, preferring repository over publisher
    # (publisher links often 403 behind JS/cookie walls).
    candidates = []
    best = data.get("best_oa_location")
    if best and best.get("url_for_pdf"):
        candidates.append(best)
    for loc in data.get("oa_locations") or []:
        if loc is not best and loc.get("url_for_pdf"):
            candidates.append(loc)

    # Sort: repository first, then publisher
    candidates.sort(key=lambda l: 0 if l.get("host_type") == "repository" else 1)

    for loc in candidates:
        pdf_url = loc["url_for_pdf"]
        body = _download_pdf(pdf_url, session)
        if body:
            return OaPdfResult(
                pdf_bytes=body,
                license=_normalize_license(loc.get("license")),
                source_api="unpaywall",
                source_url=pdf_url,
            )

    return None


# --- Provider: Europe PMC ----------------------------------------------

def _try_europepmc(
    doi: Optional[str],
    pmid: Optional[str],
    pmcid_hint: Optional[str],
    session: requests.Session,
) -> Optional[OaPdfResult]:
    """Query Europe PMC, check OA + hasPDF, then download full text PDF."""
    pmcid = pmcid_hint
    license_str = None

    if not pmcid:
        # Search for PMCID by DOI or PMID
        if doi:
            query = f"DOI:{doi}"
        elif pmid:
            query = f"EXT_ID:{pmid} AND SRC:MED"
        else:
            return None

        search_url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={quote(query)}&format=json&resultType=core"
        )
        try:
            r = session.get(search_url, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            results = r.json().get("resultList", {}).get("result", [])
        except (requests.RequestException, ValueError) as e:
            logger.debug("Europe PMC search failed: %s", e)
            return None

        if not results:
            return None
        hit = results[0]
        if hit.get("isOpenAccess") != "Y" or hit.get("hasPDF") != "Y":
            return None
        pmcid = hit.get("pmcid")
        license_str = hit.get("license")
        if not pmcid:
            return None

    # Full text PDF
    pdf_url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/"
        f"{pmcid}/fullTextPDF"
    )
    body = _download_pdf(pdf_url, session)
    if not body:
        return None

    return OaPdfResult(
        pdf_bytes=body,
        license=_normalize_license(license_str),
        source_api="europepmc",
        source_url=pdf_url,
    )


# --- Provider: Crossref -------------------------------------------------

def _try_crossref(
    doi: str, email: str, session: requests.Session,
) -> Optional[OaPdfResult]:
    """Use Crossref /works for publisher-declared TDM PDF link + license."""
    api = f"https://api.crossref.org/works/{quote(doi)}?mailto={quote(email)}"
    try:
        r = session.get(api, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json().get("message", {})
    except (requests.RequestException, ValueError) as e:
        logger.debug("Crossref lookup failed for %s: %s", doi, e)
        return None

    # Licenses live in message.license[]; TDM PDF links in message.link[]
    licenses = data.get("license") or []
    links = data.get("link") or []

    # Pick the first declared license (VoR preferred)
    license_url = None
    for lic in licenses:
        if lic.get("content-version") == "vor":
            license_url = lic.get("URL")
            break
    if license_url is None and licenses:
        license_url = licenses[0].get("URL")

    pdf_links = [l for l in links if (l.get("content-type") or "").lower() == "application/pdf"]
    for link in pdf_links:
        pdf_url = link.get("URL")
        if not pdf_url:
            continue
        body = _download_pdf(pdf_url, session)
        if body:
            return OaPdfResult(
                pdf_bytes=body,
                license=_normalize_license(license_url),
                source_api="crossref",
                source_url=pdf_url,
            )

    return None


# --- Provider: bioRxiv / medRxiv ---------------------------------------

_BIORXIV_DOI_RE = re.compile(r"^10\.1101/", re.IGNORECASE)


def _try_biorxiv(
    doi: str, session: requests.Session,
) -> Optional[OaPdfResult]:
    """Handle bioRxiv/medRxiv preprints via their details API.

    Preprints under 10.1101/... — CSHL mints the DOIs so DOI match is
    highly reliable. Both servers publish under CC licenses by default.
    """
    if not _BIORXIV_DOI_RE.match(doi):
        return None

    for server in ("biorxiv", "medrxiv"):
        api = f"https://api.biorxiv.org/details/{server}/{doi}"
        try:
            r = session.get(api, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            continue

        collection = data.get("collection") or []
        if not collection:
            continue

        # Most recent version is last in the collection
        record = collection[-1]
        version = record.get("version", "1")
        license_str = record.get("license") or "cc-by"
        # Strip DOI prefix for URL path
        doi_path = doi.split("10.1101/", 1)[1]
        pdf_url = f"https://www.{server}.org/content/10.1101/{doi_path}v{version}.full.pdf"
        body = _download_pdf(pdf_url, session)
        if body:
            return OaPdfResult(
                pdf_bytes=body,
                license=_normalize_license(license_str),
                source_api=server,
                source_url=pdf_url,
            )

    return None


# --- Orchestrator -------------------------------------------------------

def fetch_oa_pdf(
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    email: str = "",
    session: Optional[requests.Session] = None,
) -> Optional[OaPdfResult]:
    """Run the OA cascade. Returns the first successful result or None.

    Args:
        doi: Publication DOI (preferred identifier).
        pmid: Publication PMID (used to look up DOI if missing).
        email: Contact email, passed to Unpaywall + Crossref for polite
            identification. Required — these APIs throttle anonymous use.
        session: Optional pre-built requests.Session.
    """
    if not email:
        raise ValueError("email is required for polite API identification")
    if not doi and not pmid:
        return None

    own_session = session is None
    if own_session:
        session = _session(email)

    try:
        # Resolve DOI from PMID if needed; also opportunistically get PMCID.
        pmcid_hint = None
        if not doi and pmid:
            doi, pmcid_hint = pmid_to_doi(pmid, session)
        elif pmid:
            _, pmcid_hint = pmid_to_doi(pmid, session)

        if doi:
            logger.info("OA cascade starting: doi=%s pmid=%s", doi, pmid)
            result = _try_unpaywall(doi, email, session)
            if result:
                return result

        result = _try_europepmc(doi, pmid, pmcid_hint, session)
        if result:
            return result

        if doi:
            result = _try_crossref(doi, email, session)
            if result:
                return result

            result = _try_biorxiv(doi, session)
            if result:
                return result

        return None
    finally:
        if own_session:
            session.close()
