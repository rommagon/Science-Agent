"""Internal upload app for the OA-PDF pipeline.

A small Flask service exposed at ai.spotitearly.com behind Cloudflare
Access + Cloudflare Tunnel. Operators use it to upload PDFs for the
weekly must-reads that automatic OA retrieval couldn't fetch.

Entry points:
    upload_app.app:create_app    — factory used by gunicorn / tests
    upload_app.app:main          — dev server (python -m upload_app)
"""

from upload_app.app import create_app, main  # noqa: F401
