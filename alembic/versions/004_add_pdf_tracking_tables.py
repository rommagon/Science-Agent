"""Add PDF tracking tables for OA fetch pipeline

Revision ID: 004
Revises: 003
Create Date: 2026-04-12

Adds two tables supporting the Wednesday OA-PDF fetch pipeline:

- pdf_store: one row per publication for which we hold a PDF file. Records
  the filesystem path, license, and which API provided it. Used by the
  Thursday digest to decide whether to attach the PDF to the email (based
  on license) or just link to it via the Emory proxy.

- pending_fetch: one row per (publication, week) where the automated
  cascade failed or the license forbids attachment. Populated Wednesday
  18:00 UTC, updated when the founder uploads via the web app, marked as
  'cutoff' by the Thursday digest send if still missing.

No foreign keys — the publications PK column name varies across
environments (id / publication_id / pub_id per migration 003), and the
same schema-tolerance applies here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create pdf_store and pending_fetch tables."""

    # ------------------------------------------------------------------
    # pdf_store: canonical record of every PDF we hold on disk.
    # ------------------------------------------------------------------
    # publication_id is a TEXT column, not an FK, to tolerate the varying
    # PK column name on `publications`. Unique on publication_id — one PDF
    # per publication; re-fetching overwrites.
    op.create_table(
        'pdf_store',
        sa.Column('publication_id', sa.Text(), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('license', sa.Text(), nullable=True),
        sa.Column('source_api', sa.Text(), nullable=False),
        sa.Column('bytes_len', sa.Integer(), nullable=False),
        sa.Column(
            'fetched_at',
            sa.TIMESTAMP(),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('publication_id'),
    )
    op.create_index(
        'idx_pdf_store_fetched_at',
        'pdf_store',
        ['fetched_at'],
    )

    # ------------------------------------------------------------------
    # pending_fetch: (publication, week) pairs awaiting manual upload.
    # ------------------------------------------------------------------
    # Composite PK on (publication_id, week_start) so re-running the
    # Wednesday orchestrator in the same week is idempotent.
    #
    # status enum (not enforced at DB level — plain TEXT):
    #   'pending'  = alerted, no upload yet
    #   'uploaded' = PDF uploaded via web app
    #   'cutoff'   = Thursday digest sent without this PDF
    #   'attached' = picked up by digest (PDF in pdf_store, license OK)
    op.create_table(
        'pending_fetch',
        sa.Column('publication_id', sa.Text(), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('original_url', sa.Text(), nullable=True),
        sa.Column('alerted_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('uploaded_at', sa.TIMESTAMP(), nullable=True),
        sa.Column(
            'created_at',
            sa.TIMESTAMP(),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('publication_id', 'week_start'),
    )
    op.create_index(
        'idx_pending_fetch_week_start',
        'pending_fetch',
        ['week_start'],
    )
    op.create_index(
        'idx_pending_fetch_status',
        'pending_fetch',
        ['status'],
    )


def downgrade() -> None:
    """Drop PDF tracking tables."""
    op.drop_index('idx_pending_fetch_status', table_name='pending_fetch')
    op.drop_index('idx_pending_fetch_week_start', table_name='pending_fetch')
    op.drop_table('pending_fetch')
    op.drop_index('idx_pdf_store_fetched_at', table_name='pdf_store')
    op.drop_table('pdf_store')
