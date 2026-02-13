"""Add scoring columns to publications table

Revision ID: 003
Revises: 002
Create Date: 2026-02-13

Centralizes scoring data onto the publications table so it serves as the
single source of truth. Previously, scoring lived only in tri_model_events
and required multi-table JOINs. After this migration, the pipeline writes
scores directly to publications, and consumers (digest, export, etc.) can
query one table.

Includes a one-time backfill from the most recent tri_model_events row
per publication.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add scoring columns to publications and backfill from tri_model_events."""

    # Tri-model scoring columns
    op.add_column(
        'publications',
        sa.Column('final_relevancy_score', sa.Integer(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('final_relevancy_reason', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('final_summary', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('agreement_level', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('confidence', sa.Text(), nullable=True),
    )

    # Credibility columns
    op.add_column(
        'publications',
        sa.Column('credibility_score', sa.Integer(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('credibility_reason', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('credibility_confidence', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('credibility_signals_json', sa.Text(), nullable=True),
    )

    # Individual reviewer scores
    op.add_column(
        'publications',
        sa.Column('claude_score', sa.Integer(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('gemini_score', sa.Integer(), nullable=True),
    )

    # Evaluator details
    op.add_column(
        'publications',
        sa.Column('evaluator_rationale', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('disagreements', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('final_signals_json', sa.Text(), nullable=True),
    )

    # Audit: which run produced these scores and when
    op.add_column(
        'publications',
        sa.Column('scoring_run_id', sa.Text(), nullable=True),
    )
    op.add_column(
        'publications',
        sa.Column('scoring_updated_at', sa.TIMESTAMP(), nullable=True),
    )

    # Index for efficient must-reads queries (ORDER BY final_relevancy_score DESC)
    op.create_index(
        'idx_publications_final_relevancy_score',
        'publications',
        ['final_relevancy_score'],
    )

    # -------------------------------------------------------------------
    # Backfill from tri_model_events (most recent row per publication)
    # -------------------------------------------------------------------
    # Detect PK column name in publications (could be id, publication_id, or pub_id)
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'publications'"
    ))
    columns = {row[0] for row in result}

    pk_col = None
    for candidate in ("publication_id", "id", "pub_id"):
        if candidate in columns:
            pk_col = candidate
            break

    if pk_col:
        # Detect which columns exist in tri_model_events to build
        # a schema-tolerant backfill query. Some columns (like
        # final_relevancy_reason, final_summary, confidence, final_signals_json)
        # may not exist as top-level columns and need to be extracted from
        # gpt_eval_json instead.
        tme_result = conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'tri_model_events'"
        ))
        tme_columns = {row[0] for row in tme_result}

        # Build SELECT expressions for the subquery, extracting from
        # gpt_eval_json where direct columns don't exist
        def _col_or_json(col_name, json_key=None):
            """Return SQL expression: use direct column if it exists, else extract from gpt_eval_json."""
            if col_name in tme_columns:
                return col_name
            jk = json_key or col_name
            return f"gpt_eval_json::json->>'{jk}' AS {col_name}"

        select_parts = [
            "publication_id", "run_id", "created_at",
            _col_or_json("final_relevancy_score"),
            _col_or_json("final_relevancy_reason"),
            _col_or_json("final_summary"),
            _col_or_json("agreement_level"),
            _col_or_json("confidence"),
            _col_or_json("credibility_score"),
            _col_or_json("credibility_reason"),
            _col_or_json("credibility_confidence"),
            _col_or_json("credibility_signals_json"),
            _col_or_json("evaluator_rationale"),
            _col_or_json("disagreements"),
            _col_or_json("final_signals_json", "final_signals"),
        ]

        select_sql = ", ".join(select_parts)

        # Backfill main scoring columns
        conn.execute(sa.text(f"""
            UPDATE publications p
            SET
                final_relevancy_score = t.final_relevancy_score::integer,
                final_relevancy_reason = t.final_relevancy_reason,
                final_summary = t.final_summary,
                agreement_level = t.agreement_level,
                confidence = t.confidence,
                credibility_score = COALESCE(t.credibility_score::integer, p.credibility_score),
                credibility_reason = t.credibility_reason,
                credibility_confidence = t.credibility_confidence,
                credibility_signals_json = t.credibility_signals_json,
                evaluator_rationale = t.evaluator_rationale,
                disagreements = t.disagreements,
                final_signals_json = t.final_signals_json,
                scoring_run_id = t.run_id,
                scoring_updated_at = t.created_at
            FROM (
                SELECT DISTINCT ON (publication_id)
                    {select_sql}
                FROM tri_model_events
                ORDER BY publication_id, created_at DESC
            ) t
            WHERE p.{pk_col} = t.publication_id
        """))

        # Backfill individual reviewer scores from JSON columns
        try:
            conn.execute(sa.text(f"""
                UPDATE publications p
                SET
                    claude_score = (t.claude_review_json::json->>'relevancy_score')::integer,
                    gemini_score = (t.gemini_review_json::json->>'relevancy_score')::integer
                FROM (
                    SELECT DISTINCT ON (publication_id)
                        publication_id, claude_review_json, gemini_review_json
                    FROM tri_model_events
                    WHERE claude_review_json IS NOT NULL OR gemini_review_json IS NOT NULL
                    ORDER BY publication_id, created_at DESC
                ) t
                WHERE p.{pk_col} = t.publication_id
            """))
        except Exception:
            # JSON parsing may fail for some rows; non-critical
            pass


def downgrade() -> None:
    """Remove scoring columns from publications."""
    op.drop_index('idx_publications_final_relevancy_score', table_name='publications')
    op.drop_column('publications', 'scoring_updated_at')
    op.drop_column('publications', 'scoring_run_id')
    op.drop_column('publications', 'final_signals_json')
    op.drop_column('publications', 'disagreements')
    op.drop_column('publications', 'evaluator_rationale')
    op.drop_column('publications', 'gemini_score')
    op.drop_column('publications', 'claude_score')
    op.drop_column('publications', 'credibility_signals_json')
    op.drop_column('publications', 'credibility_confidence')
    op.drop_column('publications', 'credibility_reason')
    op.drop_column('publications', 'credibility_score')
    op.drop_column('publications', 'confidence')
    op.drop_column('publications', 'agreement_level')
    op.drop_column('publications', 'final_summary')
    op.drop_column('publications', 'final_relevancy_reason')
    op.drop_column('publications', 'final_relevancy_score')
