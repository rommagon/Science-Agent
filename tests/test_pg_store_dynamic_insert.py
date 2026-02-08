from storage.pg_store import _build_publications_insert_statement
from storage.pg_store import _map_publication_values
from types import SimpleNamespace


def test_build_insert_uses_publication_id_pk_and_existing_columns():
    columns = {
        "publication_id",
        "title",
        "source",
        "published_date",
        "url",
        "run_id",
    }
    sql, insert_cols = _build_publications_insert_statement(columns, "publication_id")

    assert "publication_id" in insert_cols
    assert "published_date" in insert_cols
    assert "published_at" not in insert_cols
    assert "ON CONFLICT (publication_id) DO NOTHING" in sql


def test_build_insert_skips_missing_optional_columns():
    columns = {"id", "title", "source"}
    sql, insert_cols = _build_publications_insert_statement(columns, "id")

    assert insert_cols == ["id", "title", "source"]
    assert "canonical_url" not in insert_cols
    assert "doi" not in insert_cols
    assert "ON CONFLICT (id) DO NOTHING" in sql


def test_map_values_sets_created_at_when_forced():
    pub = SimpleNamespace(
        id="pub-1",
        title="Paper",
        authors=["A", "B"],
        source="test",
        venue=None,
        date="2026-02-02",
        url="https://example.com",
        raw_text="abstract",
        summary="summary",
        source_names=[],
    )
    insert_cols = ["id", "title", "created_at"]
    values = _map_publication_values(
        pub,
        run_id="run-1",
        pk_column="id",
        insert_columns=insert_cols,
        force_python_created_at=True,
    )
    by_col = dict(zip(insert_cols, values))
    assert by_col["id"] == "pub-1"
    assert by_col["title"] == "Paper"
    assert by_col["created_at"] is not None
