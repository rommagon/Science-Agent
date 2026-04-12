"""Tests for the open-access PDF cascade in enrich/oa_pdf.py.

All HTTP calls are mocked — no network access during tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from enrich.oa_pdf import (
    ATTACHABLE_LICENSES,
    OaPdfResult,
    _download_pdf,
    _normalize_license,
    _try_biorxiv,
    _try_crossref,
    _try_europepmc,
    _try_unpaywall,
    fetch_oa_pdf,
    is_attachable_license,
    pmid_to_doi,
)


# --- Helpers ------------------------------------------------------------

MINIMAL_PDF = b"%PDF-1.4\n" + b"x" * 6000 + b"\n%%EOF"  # > MIN_PDF_BYTES


def _mock_response(status=200, json_data=None, content=b""):
    """Build a mock requests.Response-like object."""
    r = MagicMock()
    r.status_code = status
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    if json_data is not None:
        r.json = MagicMock(return_value=json_data)
    # iter_content yields the whole content in one chunk
    r.iter_content = MagicMock(return_value=iter([content]))
    return r


# --- License helpers ----------------------------------------------------

class TestLicenseNormalization:
    def test_short_form_passthrough(self):
        assert _normalize_license("cc-by") == "cc-by"
        assert _normalize_license("CC-BY") == "cc-by"

    def test_url_form_cc_by(self):
        assert _normalize_license("https://creativecommons.org/licenses/by/4.0/") == "cc-by"

    def test_url_form_cc_by_nc_nd(self):
        assert _normalize_license("http://creativecommons.org/licenses/by-nc-nd/4.0") == "cc-by-nc-nd"

    def test_url_form_cc0(self):
        assert _normalize_license("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0"

    def test_public_domain_literal(self):
        assert _normalize_license("Public Domain") == "public-domain"

    def test_missing_returns_unknown(self):
        assert _normalize_license(None) == "unknown"
        assert _normalize_license("") == "unknown"

    def test_unrecognized_returns_unknown(self):
        assert _normalize_license("proprietary") == "unknown"


class TestAttachableLicense:
    def test_cc_by_is_attachable(self):
        assert is_attachable_license("cc-by") is True

    def test_cc_by_nc_is_attachable(self):
        # Internal non-commercial use permitted
        assert is_attachable_license("cc-by-nc") is True

    def test_bronze_not_attachable(self):
        assert is_attachable_license("bronze") is False

    def test_unknown_not_attachable(self):
        assert is_attachable_license("unknown") is False

    def test_none_not_attachable(self):
        assert is_attachable_license(None) is False

    def test_attachable_set_has_expected_members(self):
        # Protection against accidental widening of the allow-list
        assert "cc-by" in ATTACHABLE_LICENSES
        assert "bronze" not in ATTACHABLE_LICENSES
        assert "unknown" not in ATTACHABLE_LICENSES


# --- Download + PDF validation -----------------------------------------

class TestDownloadPdf:
    def test_valid_pdf_accepted(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, content=MINIMAL_PDF)
        assert _download_pdf("https://x/a.pdf", session) == MINIMAL_PDF

    def test_non_200_rejected(self):
        session = MagicMock()
        session.get.return_value = _mock_response(403, content=MINIMAL_PDF)
        assert _download_pdf("https://x/a.pdf", session) is None

    def test_bad_magic_bytes_rejected(self):
        session = MagicMock()
        # HTML response mis-labelled as PDF by publisher — must be rejected
        html = b"<html>" + b"x" * 6000 + b"</html>"
        session.get.return_value = _mock_response(200, content=html)
        assert _download_pdf("https://x/a.pdf", session) is None

    def test_too_small_rejected(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, content=b"%PDF-1.4 tiny")
        assert _download_pdf("https://x/a.pdf", session) is None

    def test_network_error_returns_none(self):
        import requests

        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("boom")
        assert _download_pdf("https://x/a.pdf", session) is None


# --- PMID → DOI ---------------------------------------------------------

class TestPmidToDoi:
    def test_success(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, json_data={
            "records": [{"pmid": "12345", "doi": "10.1234/x", "pmcid": "PMC999"}]
        })
        doi, pmcid = pmid_to_doi("12345", session)
        assert doi == "10.1234/x"
        assert pmcid == "PMC999"

    def test_no_records(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, json_data={"records": []})
        assert pmid_to_doi("12345", session) == (None, None)


# --- Unpaywall ----------------------------------------------------------

class TestUnpaywall:
    def test_not_oa_returns_none(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, json_data={"is_oa": False})
        assert _try_unpaywall("10.1/x", "me@x.com", session) is None

    def test_oa_repository_hit(self):
        # Two locations — publisher and repository. Repo should be preferred.
        session = MagicMock()
        metadata = _mock_response(200, json_data={
            "is_oa": True,
            "best_oa_location": {
                "url_for_pdf": "https://publisher.example/a.pdf",
                "host_type": "publisher",
                "license": "cc-by",
            },
            "oa_locations": [
                {
                    "url_for_pdf": "https://publisher.example/a.pdf",
                    "host_type": "publisher",
                    "license": "cc-by",
                },
                {
                    "url_for_pdf": "https://repo.example/a.pdf",
                    "host_type": "repository",
                    "license": "https://creativecommons.org/licenses/by/4.0/",
                },
            ],
        })
        pdf_response = _mock_response(200, content=MINIMAL_PDF)

        call_urls = []

        def get(url, **kwargs):
            call_urls.append(url)
            if "api.unpaywall.org" in url:
                return metadata
            return pdf_response

        session.get.side_effect = get

        result = _try_unpaywall("10.1/x", "me@x.com", session)
        assert result is not None
        # Repository URL must be tried before publisher
        pdf_call_urls = [u for u in call_urls if "example" in u]
        assert pdf_call_urls[0] == "https://repo.example/a.pdf"
        assert result.source_api == "unpaywall"
        assert result.license == "cc-by"
        assert result.pdf_bytes == MINIMAL_PDF

    def test_oa_but_no_url_for_pdf(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, json_data={
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": None, "host_type": "publisher"},
            "oa_locations": [],
        })
        assert _try_unpaywall("10.1/x", "me@x.com", session) is None


# --- Europe PMC --------------------------------------------------------

class TestEuropePmc:
    def test_not_open_access_returns_none(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, json_data={
            "resultList": {"result": [{"pmcid": "PMC1", "isOpenAccess": "N", "hasPDF": "Y"}]}
        })
        assert _try_europepmc("10.1/x", None, None, session) is None

    def test_happy_path(self):
        session = MagicMock()
        metadata = _mock_response(200, json_data={
            "resultList": {"result": [{
                "pmcid": "PMC42",
                "isOpenAccess": "Y",
                "hasPDF": "Y",
                "license": "cc-by",
            }]}
        })
        pdf = _mock_response(200, content=MINIMAL_PDF)

        def get(url, **kwargs):
            return metadata if "search" in url else pdf

        session.get.side_effect = get

        result = _try_europepmc("10.1/x", None, None, session)
        assert result is not None
        assert result.source_api == "europepmc"
        assert result.license == "cc-by"

    def test_pmcid_hint_skips_search(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, content=MINIMAL_PDF)
        result = _try_europepmc(None, None, "PMC42", session)
        assert result is not None
        # Only one call (the PDF), no search
        assert session.get.call_count == 1
        assert "fullTextPDF" in session.get.call_args[0][0]


# --- Crossref ----------------------------------------------------------

class TestCrossref:
    def test_no_pdf_links_returns_none(self):
        session = MagicMock()
        session.get.return_value = _mock_response(200, json_data={
            "message": {"license": [], "link": []}
        })
        assert _try_crossref("10.1/x", "me@x.com", session) is None

    def test_picks_vor_license(self):
        session = MagicMock()
        metadata = _mock_response(200, json_data={
            "message": {
                "license": [
                    {"content-version": "am", "URL": "https://creativecommons.org/licenses/by-nc/4.0"},
                    {"content-version": "vor", "URL": "https://creativecommons.org/licenses/by/4.0"},
                ],
                "link": [
                    {"URL": "https://pub.example/a.pdf", "content-type": "application/pdf"}
                ],
            }
        })
        pdf = _mock_response(200, content=MINIMAL_PDF)

        def get(url, **kwargs):
            return metadata if "api.crossref.org" in url else pdf

        session.get.side_effect = get

        result = _try_crossref("10.1/x", "me@x.com", session)
        assert result is not None
        assert result.license == "cc-by"  # VoR license preferred
        assert result.source_api == "crossref"


# --- bioRxiv / medRxiv ------------------------------------------------

class TestBiorxiv:
    def test_non_biorxiv_doi_returns_none(self):
        session = MagicMock()
        assert _try_biorxiv("10.1038/s41586-000-0000-0", session) is None
        # Should not have made any HTTP calls
        assert session.get.call_count == 0

    def test_biorxiv_happy_path(self):
        session = MagicMock()
        details = _mock_response(200, json_data={
            "collection": [
                {"version": "1", "license": "cc-by"},
                {"version": "2", "license": "cc-by"},
            ]
        })
        pdf = _mock_response(200, content=MINIMAL_PDF)

        def get(url, **kwargs):
            return details if "api.biorxiv.org" in url else pdf

        session.get.side_effect = get

        result = _try_biorxiv("10.1101/2024.01.01.000001", session)
        assert result is not None
        assert result.source_api == "biorxiv"
        assert "v2.full.pdf" in result.source_url  # latest version used


# --- Orchestrator -----------------------------------------------------

class TestFetchOaPdf:
    def test_requires_email(self):
        with pytest.raises(ValueError):
            fetch_oa_pdf(doi="10.1/x", email="")

    def test_no_identifiers_returns_none(self):
        assert fetch_oa_pdf(email="me@x.com") is None

    def test_cascade_short_circuits_on_first_hit(self):
        """Unpaywall succeeds — later providers must not be called."""
        result = OaPdfResult(
            pdf_bytes=MINIMAL_PDF,
            license="cc-by",
            source_api="unpaywall",
            source_url="https://x/a.pdf",
        )
        with patch("enrich.oa_pdf._try_unpaywall", return_value=result) as mock_unp, \
             patch("enrich.oa_pdf._try_europepmc") as mock_epmc, \
             patch("enrich.oa_pdf._try_crossref") as mock_cr, \
             patch("enrich.oa_pdf._try_biorxiv") as mock_bio:
            out = fetch_oa_pdf(doi="10.1/x", email="me@x.com")
            assert out is result
            mock_unp.assert_called_once()
            mock_epmc.assert_not_called()
            mock_cr.assert_not_called()
            mock_bio.assert_not_called()

    def test_cascade_falls_through(self):
        with patch("enrich.oa_pdf._try_unpaywall", return_value=None), \
             patch("enrich.oa_pdf._try_europepmc", return_value=None), \
             patch("enrich.oa_pdf._try_crossref", return_value=None), \
             patch("enrich.oa_pdf._try_biorxiv", return_value=None):
            assert fetch_oa_pdf(doi="10.1/x", email="me@x.com") is None

    def test_pmid_only_triggers_doi_lookup(self):
        with patch("enrich.oa_pdf.pmid_to_doi", return_value=("10.1/resolved", "PMC1")) as mock_conv, \
             patch("enrich.oa_pdf._try_unpaywall", return_value=None) as mock_unp, \
             patch("enrich.oa_pdf._try_europepmc", return_value=None), \
             patch("enrich.oa_pdf._try_crossref", return_value=None), \
             patch("enrich.oa_pdf._try_biorxiv", return_value=None):
            fetch_oa_pdf(pmid="12345", email="me@x.com")
            mock_conv.assert_called_once()
            # Unpaywall must receive the resolved DOI
            assert mock_unp.call_args[0][0] == "10.1/resolved"


class TestOaPdfResult:
    def test_sha256_is_stable(self):
        r = OaPdfResult(pdf_bytes=MINIMAL_PDF, license="cc-by",
                        source_api="unpaywall", source_url="https://x")
        assert len(r.sha256) == 64
        assert r.sha256 == OaPdfResult(
            pdf_bytes=MINIMAL_PDF, license="different",
            source_api="x", source_url="y").sha256  # sha doesn't depend on metadata

    def test_attachable_flag(self):
        r = OaPdfResult(pdf_bytes=b"", license="cc-by",
                        source_api="x", source_url="y")
        assert r.attachable is True

        r2 = OaPdfResult(pdf_bytes=b"", license="bronze",
                         source_api="x", source_url="y")
        assert r2.attachable is False
