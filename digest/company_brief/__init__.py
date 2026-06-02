"""Company Brief aggregator.

Assembles the weekly SpotitEarly Company Brief — one branded email that
consolidates three tools in a fixed order:

    1. Science Agent articles  (this repo; in-process)
    2. Grant Agent funding      (HTTP: GET /api/brief)
    3. Regulatory updates       (HTTP: GET /api/summaries/brief)

Every section conforms to the shared contract in :mod:`contract`. The
entrypoint is :func:`aggregate.build_company_brief`, rendered by
:func:`render.render_company_brief` and sent by the existing
``digest.senders.GmailSender`` (see ``scripts/generate_company_brief.py``).
"""
