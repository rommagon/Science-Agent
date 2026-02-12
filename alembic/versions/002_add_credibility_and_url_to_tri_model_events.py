"""Add credibility and url columns to tri_model_events

Revision ID: 002
Revises: 001
Create Date: 2026-02-12

The tri_model_events table was missing columns for url and credibility data.
The runner already collects this data but it was silently dropped because the
columns didn't exist. This migration adds them so they can be persisted and
exported to the backend via JSONL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add url and credibility columns to tri_model_events."""

    # URL column — stores the publication URL so JSONL export can include it
    # without needing a JOIN to the publications table.
    op.add_column(
        'tri_model_events',
        sa.Column('url', sa.Text(), nullable=True),
    )

    # Credibility columns — these were already computed by the runner but
    # silently dropped because the table had no columns for them.
    op.add_column(
        'tri_model_events',
        sa.Column('credibility_score', sa.Integer(), nullable=True),
    )
    op.add_column(
        'tri_model_events',
        sa.Column('credibility_reason', sa.Text(), nullable=True),
    )
    op.add_column(
        'tri_model_events',
        sa.Column('credibility_confidence', sa.Text(), nullable=True),
    )
    op.add_column(
        'tri_model_events',
        sa.Column('credibility_signals_json', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove url and credibility columns from tri_model_events."""
    op.drop_column('tri_model_events', 'credibility_signals_json')
    op.drop_column('tri_model_events', 'credibility_confidence')
    op.drop_column('tri_model_events', 'credibility_reason')
    op.drop_column('tri_model_events', 'credibility_score')
    op.drop_column('tri_model_events', 'url')
