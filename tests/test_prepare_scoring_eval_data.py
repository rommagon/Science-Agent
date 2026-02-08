import sqlite3

from scripts.prepare_scoring_eval_data import (
    enrich_items_from_db,
    normalize_title,
    map_rating_to_0_3,
    prepare_dataset,
)


def test_title_normalization_matching():
    udi = [
        {"title": "A Study: On Cancer!", "rank": 72, "explanation": "ranked"}
    ]
    survey = [
        {
            "publication_id": "",
            "title": "a study on cancer",
            "final_relevancy_score": 80,
            "human_score": 75,
            "reasoning": "good",
            "evaluator": "Alex",
            "confidence": "medium",
        }
    ]

    dataset, summary = prepare_dataset(udi, survey)

    assert summary.num_pubs_total == 1
    assert summary.num_matched_by_title == 1
    assert len(dataset[0]["labels"]) == 2


def test_rating_mapping():
    assert map_rating_to_0_3(0) == 0
    assert map_rating_to_0_3(24) == 0
    assert map_rating_to_0_3(25) == 1
    assert map_rating_to_0_3(49) == 1
    assert map_rating_to_0_3(50) == 2
    assert map_rating_to_0_3(74) == 2
    assert map_rating_to_0_3(75) == 3
    assert map_rating_to_0_3(100) == 3


def test_merge_behavior_by_publication_id():
    udi = [
        {"title": "Paper One", "rank": 10, "explanation": "low rank"}
    ]
    survey = [
        {
            "publication_id": "PUB-123",
            "title": "Paper One",
            "final_relevancy_score": 55,
            "human_score": 20,
            "reasoning": "ok",
            "evaluator": "Sam",
            "confidence": "low",
        }
    ]

    dataset, summary = prepare_dataset(udi, survey)

    assert summary.num_pubs_total == 1
    assert summary.num_matched_by_title == 1
    assert len(dataset[0]["labels"]) == 2
    assert normalize_title(dataset[0]["title"]) == "paper one"


def test_enrich_items_from_db(tmp_path):
    db_path = tmp_path / "acitrack.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE publications (
            id TEXT PRIMARY KEY,
            doi TEXT,
            pmid TEXT,
            url TEXT,
            canonical_url TEXT,
            published_date TEXT,
            source TEXT,
            venue TEXT,
            venue_name TEXT,
            raw_text TEXT,
            summary TEXT
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO publications (
            id, doi, pmid, url, canonical_url, published_date,
            source, venue, venue_name, raw_text, summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "PUB-1",
            "10.1000/xyz",
            "12345",
            "https://example.com",
            "https://canonical.example.com",
            "2026-01-01",
            "pubmed",
            "Test Venue",
            "Venue Alt",
            "Abstract text",
            "Summary text",
        ),
    )
    conn.commit()
    conn.close()

    items = [{"publication_id": "PUB-1", "title": "Paper One", "labels": []}]
    enriched = enrich_items_from_db(items, str(db_path))

    assert enriched[0]["doi"] == "10.1000/xyz"
    assert enriched[0]["pmid"] == "12345"
    assert enriched[0]["canonical_url"] == "https://canonical.example.com"
    assert enriched[0]["published_date"] == "2026-01-01"
    assert enriched[0]["source"] == "pubmed"
    assert enriched[0]["venue"] == "Test Venue"
    assert enriched[0]["abstract"] == "Abstract text"
