from scoring_eval.datasets import enrich_items_with_tri_model


def test_enrich_items_with_tri_model_matching_order():
    tri_results = [
        {
            "publication_id": "PUB-1",
            "doi": "10.1000/abc",
            "pmid": "111",
            "title": "First Paper",
            "final_relevancy_score": 90,
        },
        {
            "publication_id": "PUB-2",
            "doi": "10.1000/def",
            "pmid": "222",
            "canonical_url": "https://example.com/paper-two",
            "title": "Second Paper",
            "final_relevancy_score": 70,
        },
    ]

    items = [
        {"publication_id": "PUB-1", "title": "First Paper"},
        {"publication_id": "", "doi": "10.1000/def", "title": "Other"},
        {"pmid": "111", "title": "Mismatch"},
        {"canonical_url": "https://example.com/paper-two/", "title": "Not Matching Title"},
    ]

    enriched = enrich_items_with_tri_model(items, tri_results)

    assert enriched[0]["model_score"] == 90
    assert enriched[1]["model_score"] == 70
    assert enriched[2]["model_score"] == 90
    assert enriched[3]["model_score"] == 70
