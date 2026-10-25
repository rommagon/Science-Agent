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
    assert "ON CONFLICT (publication_id) DO UPDATE SET" in sql
    assert "COALESCE(EXCLUDED.url, publications.url)" in sql


def test_build_insert_skips_missing_optional_columns():
    columns = {"id", "title", "source"}
    sql, insert_cols = _build_publications_insert_statement(columns, "id")

    assert insert_cols == ["id", "title", "source"]
    assert "canonical_url" not in insert_cols
    assert "doi" not in insert_cols
    # No URL-related columns present, so falls back to DO NOTHING
    assert "ON CONFLICT (id) DO NOTHING" in sql


def test_store_publications_stats_distinguish_inserts_updates_errors(monkeypatch):
    import storage.pg_store as pg_store

    class FakeCursor:
        def __init__(self, behaviors):
            self.behaviors = behaviors
            self._last_result = None
            self.closed = False

        def execute(self, sql, params=None):
            if sql.lstrip().upper().startswith("INSERT"):
                behavior = self.behaviors.pop(0)
                if behavior == "error":
                    raise RuntimeError("boom")
                # RETURNING (xmax = 0): True for fresh insert, False for update
                self._last_result = (behavior == "insert",)

        def fetchone(self):
            return self._last_result

        def close(self):
            self.closed = True

    class FakeConn:
        def __init__(self, cursor):
            self._cursor = cursor
            self.committed = False

        def cursor(self):
            return self._cursor

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

    cursor = FakeCursor(["insert", "update", "error"])
    conn = FakeConn(cursor)
    returned = []

    monkeypatch.setattr(pg_store, "_get_connection", lambda url: conn)
    monkeypatch.setattr(pg_store, "_put_connection", lambda c: returned.append(c))
    monkeypatch.setattr(
        pg_store,
        "_get_publications_table_metadata",
        lambda c, url: ({"id", "title", "source", "url", "run_id"}, "id", False, False),
    )

    pubs = [
        SimpleNamespace(
            id=f"p{i}",
            title="T",
            authors=[],
            source="s",
            venue=None,
            date=None,
            url="https://x",
            raw_text=None,
            summary=None,
            source_names=[],
        )
        for i in range(3)
    ]

    result = pg_store.store_publications(pubs, "run-1", "postgresql://fake")

    assert result["success"] is True
    assert result["inserted"] == 1
    assert result["duplicates"] == 1
    assert result["errors"] == 1
    assert conn.committed
    # Connection and cursor must always be released
    assert cursor.closed
    assert returned == [conn]


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


def test_export_tri_model_events_serializes_non_created_at_datetimes(monkeypatch, tmp_path):
    """published_date (and other timestamp columns) come back from psycopg2 as
    datetime/date objects; the export must write them as ISO strings instead of
    failing with "Object of type datetime is not JSON serializable"."""
    import json
    from datetime import date, datetime, timezone
    from decimal import Decimal

    import storage.pg_store as pg_store

    created = datetime(2026, 10, 1, 15, 45, tzinfo=timezone.utc)
    published = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

    class FakeCursor:
        def __init__(self):
            self.closed = False

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            # Column order follows desired_columns filtered by available_columns:
            # run_id, publication_id, published_date, gemini_review_json,
            # final_relevancy_score, credibility_score, created_at
            return [
                ("run-1", "pub-1", published, '{"score": 80}', 80, Decimal("0.5"), created),
                ("run-1", "pub-2", date(2026, 9, 29), None, 40, None, created),
            ]

        def close(self):
            self.closed = True

    class FakeConn:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    cursor = FakeCursor()
    conn = FakeConn(cursor)
    monkeypatch.setattr(pg_store, "_get_connection", lambda url: conn)
    monkeypatch.setattr(pg_store, "_put_connection", lambda c: None)
    monkeypatch.setattr(
        pg_store,
        "_get_tri_model_events_columns",
        lambda c, url: {
            "run_id", "publication_id", "published_date", "gemini_review_json",
            "final_relevancy_score", "credibility_score", "created_at",
        },
    )
    monkeypatch.setattr(
        pg_store,
        "_get_publications_table_metadata",
        lambda c, url: (set(), None, False, False),
    )

    out = tmp_path / "events.jsonl"
    result = pg_store.export_tri_model_events_to_jsonl("run-1", str(out), "postgresql://fake")

    assert result["success"] is True, result["error"]
    assert result["events_exported"] == 2
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["published_date"] == published.isoformat()
    assert rows[0]["created_at"] == created.isoformat()
    assert rows[0]["gemini_review"] == {"score": 80}
    assert rows[0]["credibility_score"] == "0.5"
    assert rows[1]["published_date"] == "2026-09-29"
    assert rows[1]["gemini_review"] is None
    assert cursor.closed
