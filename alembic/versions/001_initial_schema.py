"""Initial schema for acitrack database

Revision ID: 001
Revises:
Create Date: 2026-01-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial schema with papers, runs, run_papers, relevancy_events, and tri_model_events tables."""

    # Create papers table
    op.create_table(
        'papers',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('authors', sa.Text(), nullable=True),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('venue', sa.Text(), nullable=True),
        sa.Column('published_at', sa.Text(), nullable=True),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('source_names', sa.Text(), nullable=True),
        sa.Column('doi', sa.Text(), nullable=True),
        sa.Column('citation_count', sa.Integer(), nullable=True),
        sa.Column('citations_per_year', sa.Float(), nullable=True),
        sa.Column('venue_name', sa.Text(), nullable=True),
        sa.Column('pub_type', sa.Text(), nullable=True),
        sa.Column('relevance_score', sa.Integer(), nullable=True),
        sa.Column('credibility_score', sa.Integer(), nullable=True),
        sa.Column('main_interesting_fact', sa.Text(), nullable=True),
        sa.Column('relevance_to_spotitearly', sa.Text(), nullable=True),
        sa.Column('modality_tags', sa.Text(), nullable=True),
        sa.Column('sample_size', sa.Text(), nullable=True),
        sa.Column('study_type', sa.Text(), nullable=True),
        sa.Column('key_metrics', sa.Text(), nullable=True),
        sa.Column('sponsor_flag', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for papers
    op.create_index('idx_papers_published_at', 'papers', ['published_at'])
    op.create_index('idx_papers_source', 'papers', ['source'])
    op.create_index('idx_papers_run_id', 'papers', ['run_id'])
    op.create_index('idx_papers_created_at', 'papers', ['created_at'])

    # Create runs table
    op.create_table(
        'runs',
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), nullable=True),
        sa.Column('started_at', sa.Text(), nullable=False),
        sa.Column('window_start', sa.TIMESTAMP(), nullable=True),
        sa.Column('window_end', sa.TIMESTAMP(), nullable=True),
        sa.Column('since_timestamp', sa.Text(), nullable=True),
        sa.Column('max_items_per_source', sa.Integer(), nullable=True),
        sa.Column('sources_count', sa.Integer(), nullable=True),
        sa.Column('total_fetched', sa.Integer(), nullable=True),
        sa.Column('total_deduped', sa.Integer(), nullable=True),
        sa.Column('new_count', sa.Integer(), nullable=True),
        sa.Column('unchanged_count', sa.Integer(), nullable=True),
        sa.Column('summarized_count', sa.Integer(), nullable=True),
        sa.Column('upload_drive', sa.Boolean(), nullable=True, default=False),
        sa.PrimaryKeyConstraint('run_id')
    )

    # Create indexes for runs
    op.create_index('idx_runs_mode_window_end', 'runs', ['mode', 'window_end'])
    op.create_index('idx_runs_started_at', 'runs', ['started_at'])

    # Create run_papers table (junction table for runs and papers)
    op.create_table(
        'run_papers',
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('pub_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('published_at', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('run_id', 'pub_id')
    )

    # Create indexes for run_papers
    op.create_index('idx_run_papers_run_id', 'run_papers', ['run_id'])
    op.create_index('idx_run_papers_status', 'run_papers', ['status'])
    op.create_index('idx_run_papers_source', 'run_papers', ['source'])
    op.create_index('idx_run_papers_published_at', 'run_papers', ['published_at'])

    # Create relevancy_events table
    op.create_table(
        'relevancy_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), nullable=False),
        sa.Column('publication_id', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('prompt_version', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('relevancy_score', sa.Integer(), nullable=True),
        sa.Column('relevancy_reason', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Text(), nullable=True),
        sa.Column('signals_json', sa.Text(), nullable=True),
        sa.Column('input_fingerprint', sa.Text(), nullable=True),
        sa.Column('raw_response_json', sa.Text(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'publication_id', 'prompt_version', name='uq_relevancy_events_run_pub_prompt')
    )

    # Create indexes for relevancy_events
    op.create_index('idx_relevancy_events_run_id', 'relevancy_events', ['run_id'])
    op.create_index('idx_relevancy_events_pub_id', 'relevancy_events', ['publication_id'])
    op.create_index('idx_relevancy_events_created_at', 'relevancy_events', ['created_at'])
    op.create_index('idx_relevancy_events_mode', 'relevancy_events', ['mode'])

    # Create tri_model_events table
    op.create_table(
        'tri_model_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), nullable=False),
        sa.Column('publication_id', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('published_date', sa.Text(), nullable=True),
        sa.Column('claude_review_json', sa.Text(), nullable=True),
        sa.Column('gemini_review_json', sa.Text(), nullable=True),
        sa.Column('gpt_eval_json', sa.Text(), nullable=True),
        sa.Column('final_relevancy_score', sa.Integer(), nullable=True),
        sa.Column('final_relevancy_reason', sa.Text(), nullable=True),
        sa.Column('final_signals_json', sa.Text(), nullable=True),
        sa.Column('final_summary', sa.Text(), nullable=True),
        sa.Column('agreement_level', sa.Text(), nullable=True),
        sa.Column('disagreements', sa.Text(), nullable=True),
        sa.Column('evaluator_rationale', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Text(), nullable=True),
        sa.Column('prompt_versions_json', sa.Text(), nullable=True),
        sa.Column('model_names_json', sa.Text(), nullable=True),
        sa.Column('claude_latency_ms', sa.Integer(), nullable=True),
        sa.Column('gemini_latency_ms', sa.Integer(), nullable=True),
        sa.Column('gpt_latency_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'publication_id', name='uq_tri_model_events_run_pub')
    )

    # Create indexes for tri_model_events
    op.create_index('idx_tri_model_events_run_id', 'tri_model_events', ['run_id'])
    op.create_index('idx_tri_model_events_pub_id', 'tri_model_events', ['publication_id'])
    op.create_index('idx_tri_model_events_created_at', 'tri_model_events', ['created_at'])
    op.create_index('idx_tri_model_events_mode', 'tri_model_events', ['mode'])
    op.create_index('idx_tri_model_events_final_score', 'tri_model_events', ['final_relevancy_score'])


def downgrade() -> None:
    """Drop all tables and indexes."""

    # Drop tri_model_events table and indexes
    op.drop_index('idx_tri_model_events_final_score', table_name='tri_model_events')
    op.drop_index('idx_tri_model_events_mode', table_name='tri_model_events')
    op.drop_index('idx_tri_model_events_created_at', table_name='tri_model_events')
    op.drop_index('idx_tri_model_events_pub_id', table_name='tri_model_events')
    op.drop_index('idx_tri_model_events_run_id', table_name='tri_model_events')
    op.drop_table('tri_model_events')

    # Drop relevancy_events table and indexes
    op.drop_index('idx_relevancy_events_mode', table_name='relevancy_events')
    op.drop_index('idx_relevancy_events_created_at', table_name='relevancy_events')
    op.drop_index('idx_relevancy_events_pub_id', table_name='relevancy_events')
    op.drop_index('idx_relevancy_events_run_id', table_name='relevancy_events')
    op.drop_table('relevancy_events')

    # Drop run_papers table and indexes
    op.drop_index('idx_run_papers_published_at', table_name='run_papers')
    op.drop_index('idx_run_papers_source', table_name='run_papers')
    op.drop_index('idx_run_papers_status', table_name='run_papers')
    op.drop_index('idx_run_papers_run_id', table_name='run_papers')
    op.drop_table('run_papers')

    # Drop runs table and indexes
    op.drop_index('idx_runs_started_at', table_name='runs')
    op.drop_index('idx_runs_mode_window_end', table_name='runs')
    op.drop_table('runs')

    # Drop papers table and indexes
    op.drop_index('idx_papers_created_at', table_name='papers')
    op.drop_index('idx_papers_run_id', table_name='papers')
    op.drop_index('idx_papers_source', table_name='papers')
    op.drop_index('idx_papers_published_at', table_name='papers')
    op.drop_table('papers')
