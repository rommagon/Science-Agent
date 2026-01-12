"""Multi-lane paper expansion for aggressive candidate discovery.

Implements four expansion lanes:
A) PubMed neighbor expansion (NCBI ELink)
B) Bibliometrics-based expansion (cited-by, references, related)
C) LLM query expansion (structured boolean queries)
D) Entity/venue watchlist expansion

All lanes produce Publication objects that merge into the main pipeline.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import List, Set, Optional, Dict, Tuple
import requests

from acitrack_types import Publication, compute_id
from bibliometrics.adapters import enrich_publication, resolve_ids_to_identifiers, resolve_doi_to_pmid
from config.expansion_config import (
    ENABLE_PUBMED_NEIGHBORS,
    ENABLE_BIBLIOMETRICS,
    ENABLE_LLM_QUERIES,
    ENABLE_WATCHLIST,
    PUBMED_NEIGHBORS_TOP_K,
    PUBMED_NEIGHBORS_PER_SEED,
    BIBLIO_MAX_CITED_BY_PER_SEED,
    BIBLIO_MAX_REFERENCES_PER_SEED,
    BIBLIO_MAX_RELATED_PER_SEED,
    LLM_EXPANSION_MAX_QUERIES,
    LLM_EXPANSION_MAX_RESULTS_PER_QUERY,
)
from diff.dedupe import extract_doi, extract_pmid
from scoring.relevance import compute_relevance_score

logger = logging.getLogger(__name__)

# Cache directory for ELink API responses
CACHE_DIR = "data/cache/expansion"
os.makedirs(CACHE_DIR, exist_ok=True)

# NCBI ELink configuration
ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_POLITENESS_DELAY = 0.34  # seconds


def expand_papers(
    seed_publications: List[Publication],
    run_id: str,
    since_date: datetime,
) -> tuple[List[Publication], Dict[str, int]]:
    """Run all expansion lanes and return discovered papers.

    Args:
        seed_publications: Initial publications from core sources
        run_id: Run identifier
        since_date: Only include papers newer than this

    Returns:
        Tuple of (expanded_publications, lane_stats)
        lane_stats: dict with counts per lane
    """
    logger.info("Starting multi-lane expansion with %d seed publications", len(seed_publications))

    expanded_pubs = []
    stats = {
        "pubmed_neighbors": 0,
        "bibliometrics_cited_by": 0,
        "bibliometrics_references": 0,
        "llm_queries": 0,
        "watchlist": 0,
    }

    # Lane A: PubMed neighbor expansion
    if ENABLE_PUBMED_NEIGHBORS:
        logger.info("Lane A: PubMed neighbor expansion")
        neighbors = _expand_pubmed_neighbors(seed_publications, run_id, since_date)
        expanded_pubs.extend(neighbors)
        stats["pubmed_neighbors"] = len(neighbors)
        logger.info("PubMed neighbors: discovered %d papers", len(neighbors))

    # Lane B: Bibliometrics expansion
    if ENABLE_BIBLIOMETRICS:
        logger.info("Lane B: Bibliometrics expansion (cited-by + references)")
        biblio_pubs = _expand_bibliometrics(seed_publications, run_id, since_date)
        expanded_pubs.extend(biblio_pubs)
        stats["bibliometrics_cited_by"] = len([p for p in biblio_pubs if "cited-by" in p.source.lower()])
        stats["bibliometrics_references"] = len([p for p in biblio_pubs if "references" in p.source.lower()])
        logger.info("Bibliometrics: discovered %d papers", len(biblio_pubs))

    # Lane C: LLM query expansion
    if ENABLE_LLM_QUERIES:
        logger.info("Lane C: LLM query expansion")
        llm_pubs = _expand_llm_queries(seed_publications, run_id, since_date)
        expanded_pubs.extend(llm_pubs)
        stats["llm_queries"] = len(llm_pubs)
        logger.info("LLM queries: discovered %d papers", len(llm_pubs))

    # Lane D: Entity/venue watchlist
    if ENABLE_WATCHLIST:
        logger.info("Lane D: Entity/venue watchlist expansion")
        watchlist_pubs = _expand_watchlist(run_id, since_date)
        expanded_pubs.extend(watchlist_pubs)
        stats["watchlist"] = len(watchlist_pubs)
        logger.info("Watchlist: discovered %d papers", len(watchlist_pubs))

    logger.info("Expansion complete: discovered %d total papers across all lanes", len(expanded_pubs))
    return expanded_pubs, stats


def _expand_pubmed_neighbors(
    seed_publications: List[Publication],
    run_id: str,
    since_date: datetime,
) -> List[Publication]:
    """Expand using PubMed ELink neighbor links.

    FIX: Filter seeds by PMID availability BEFORE ranking by relevance.

    Args:
        seed_publications: Seed papers
        run_id: Run ID
        since_date: Date filter

    Returns:
        List of neighbor publications
    """
    logger.info("Lane A (PubMed neighbors): Starting with %d total seed publications", len(seed_publications))

    # STEP 1: Filter seeds to only those with PMIDs
    seeds_with_pmids = []
    for pub in seed_publications:
        pmid = extract_pmid(pub)  # Use robust extraction from diff.dedupe
        if pmid:
            seeds_with_pmids.append((pub, pmid))

    logger.info("Lane A: Found %d/%d seeds with valid PMIDs", len(seeds_with_pmids), len(seed_publications))

    if not seeds_with_pmids:
        logger.warning("Lane A: No seeds with PMIDs, skipping PubMed neighbor expansion")
        return []

    # STEP 2: Compute relevance scores ONLY for seeds with PMIDs
    seeds_with_scores = []
    for pub, pmid in seeds_with_pmids:
        relevance = compute_relevance_score(pub.title, pub.raw_text)
        seeds_with_scores.append((pub, pmid, relevance["score"]))

    # STEP 3: Rank by relevance and select top K
    seeds_with_scores.sort(key=lambda x: x[2], reverse=True)
    top_seeds = seeds_with_scores[:PUBMED_NEIGHBORS_TOP_K]

    logger.info(
        "Lane A: Selected top %d seeds by relevance (scores: %s)",
        len(top_seeds),
        [f"{score:.0f}" for _, _, score in top_seeds[:5]]
    )

    # STEP 4: Extract PMIDs from top seeds (now guaranteed to have them)
    seed_pmids = [pmid for _, pmid, _ in top_seeds]

    logger.info("Lane A: Using %d seed PMIDs for expansion: %s", len(seed_pmids), seed_pmids[:5])

    # STEP 5: Fetch neighbors for each seed PMID with caching
    all_neighbor_pmids = set()

    for pub, pmid, score in top_seeds[:10]:  # Limit to avoid excessive API calls
        try:
            # Check cache first
            cache_key = _get_elink_cache_key(pmid)
            cached_neighbors = _load_cached_elink(cache_key)

            if cached_neighbors is not None:
                all_neighbor_pmids.update(cached_neighbors)
                logger.debug("Lane A: Seed PMID %s: loaded %d neighbors from cache", pmid, len(cached_neighbors))
                continue

            # Not cached, make API call
            time.sleep(NCBI_POLITENESS_DELAY)

            # ELink to get related PMIDs
            params = {
                "dbfrom": "pubmed",
                "db": "pubmed",
                "id": pmid,
                "linkname": "pubmed_pubmed",  # Related articles
                "retmode": "json",
            }

            response = requests.get(ELINK_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            # Extract neighbor PMIDs
            neighbor_pmids = []
            linksets = data.get("linksets", [])
            if linksets and "linksetdbs" in linksets[0]:
                for linksetdb in linksets[0]["linksetdbs"]:
                    if linksetdb.get("linkname") == "pubmed_pubmed":
                        links = linksetdb.get("links", [])
                        neighbor_pmids = [str(link) for link in links[:PUBMED_NEIGHBORS_PER_SEED]]
                        break

            # Cache the result
            _save_cached_elink(cache_key, neighbor_pmids)

            all_neighbor_pmids.update(neighbor_pmids)
            logger.debug("Lane A: Seed PMID %s (score=%.0f, title='%s'): found %d neighbors",
                        pmid, score, pub.title[:50], len(neighbor_pmids))

        except Exception as e:
            logger.warning("Lane A: ELink failed for PMID %s: %s", pmid, e)
            continue

    logger.info("Lane A: Collected %d unique neighbor PMIDs from %d seeds", len(all_neighbor_pmids), len(top_seeds))

    # STEP 6: Fetch metadata for neighbor PMIDs using ESummary
    neighbor_pubs = _fetch_pubmed_by_pmids(list(all_neighbor_pmids), run_id, "PubMed-Neighbors", since_date)

    logger.info("Lane A: Fetched %d neighbor publications", len(neighbor_pubs))
    return neighbor_pubs


def _expand_bibliometrics(
    seed_publications: List[Publication],
    run_id: str,
    since_date: datetime,
) -> List[Publication]:
    """Expand using bibliometrics API (cited-by + references).

    FIX: Filter seeds by DOI/PMID availability BEFORE ranking by relevance.

    Args:
        seed_publications: Seed papers
        run_id: Run ID
        since_date: Date filter

    Returns:
        List of citing/referenced publications
    """
    logger.info("Lane B (Bibliometrics): Starting with %d total seed publications", len(seed_publications))

    # STEP 1: Filter seeds to only those with DOI OR PMID
    seeds_with_identifiers = []
    for pub in seed_publications:
        doi = extract_doi(pub)  # Use robust extraction from diff.dedupe
        pmid = extract_pmid(pub)

        if doi or pmid:
            seeds_with_identifiers.append((pub, doi, pmid))

    logger.info("Lane B: Found %d/%d seeds with valid DOI or PMID", len(seeds_with_identifiers), len(seed_publications))

    if not seeds_with_identifiers:
        logger.warning("Lane B: No seeds with DOI/PMID, skipping bibliometrics expansion")
        return []

    # STEP 2: Compute relevance scores ONLY for seeds with identifiers
    seeds_with_scores = []
    for pub, doi, pmid in seeds_with_identifiers:
        relevance = compute_relevance_score(pub.title, pub.raw_text)
        seeds_with_scores.append((pub, doi, pmid, relevance["score"]))

    # STEP 3: Rank by relevance and select top K
    seeds_with_scores.sort(key=lambda x: x[3], reverse=True)
    top_seeds = seeds_with_scores[:20]  # Limit to top 20

    logger.info(
        "Lane B: Selected top %d seeds by relevance (scores: %s)",
        len(top_seeds),
        [f"{score:.0f}" for _, _, _, score in top_seeds[:5]]
    )

    # STEP 4: Expand using bibliometrics API
    all_biblio_pubs = []
    total_cited_by_ids = 0
    total_references_ids = 0
    total_cited_by_fetched = 0
    total_references_fetched = 0
    seeds_with_data = 0

    for pub, doi, pmid, score in top_seeds:
        logger.debug("Lane B: Processing seed (score=%.0f, doi=%s, pmid=%s, title='%s')",
                    score, doi, pmid, pub.title[:50])

        # Fetch bibliometrics data
        try:
            biblio_metrics = enrich_publication(
                doi=doi,
                pmid=pmid,
                title=pub.title,
                max_cited_by=BIBLIO_MAX_CITED_BY_PER_SEED,
                max_references=BIBLIO_MAX_REFERENCES_PER_SEED,
                max_related=BIBLIO_MAX_RELATED_PER_SEED,
            )

            if not biblio_metrics:
                logger.debug("Lane B: No bibliometrics data for doi=%s, pmid=%s", doi, pmid)
                continue

            seeds_with_data += 1
            cited_by_count = len(biblio_metrics.cited_by_ids) if biblio_metrics.cited_by_ids else 0
            references_count = len(biblio_metrics.references_ids) if biblio_metrics.references_ids else 0

            logger.debug(
                "Lane B: Seed '%s' (API=%s) -> %d citations, %d references",
                pub.title[:50],
                biblio_metrics.source_api,
                cited_by_count,
                references_count
            )

            # Fetch metadata for cited-by papers
            if biblio_metrics.cited_by_ids:
                total_cited_by_ids += len(biblio_metrics.cited_by_ids)
                cited_by_pubs = _fetch_papers_by_ids(
                    biblio_metrics.cited_by_ids,
                    run_id,
                    f"Bibliometrics-CitedBy-{biblio_metrics.source_api}",
                    since_date
                )
                all_biblio_pubs.extend(cited_by_pubs)
                total_cited_by_fetched += len(cited_by_pubs)
                logger.debug("Lane B: Fetched %d/%d citing papers for seed '%s'",
                           len(cited_by_pubs), len(biblio_metrics.cited_by_ids), pub.title[:50])

            # Fetch metadata for references
            if biblio_metrics.references_ids:
                total_references_ids += len(biblio_metrics.references_ids)
                ref_pubs = _fetch_papers_by_ids(
                    biblio_metrics.references_ids,
                    run_id,
                    f"Bibliometrics-References-{biblio_metrics.source_api}",
                    since_date
                )
                all_biblio_pubs.extend(ref_pubs)
                total_references_fetched += len(ref_pubs)
                logger.debug("Lane B: Fetched %d/%d references for seed '%s'",
                           len(ref_pubs), len(biblio_metrics.references_ids), pub.title[:50])

        except Exception as e:
            logger.warning("Lane B: Bibliometrics expansion failed for %s: %s", pub.title[:60], e)
            continue

    logger.info(
        "Lane B Summary: %d/%d seeds with data | "
        "Citations: %d IDs -> %d fetched | "
        "References: %d IDs -> %d fetched | "
        "Total papers: %d",
        seeds_with_data,
        len(top_seeds),
        total_cited_by_ids,
        total_cited_by_fetched,
        total_references_ids,
        total_references_fetched,
        len(all_biblio_pubs)
    )
    return all_biblio_pubs


def _expand_llm_queries(
    seed_publications: List[Publication],
    run_id: str,
    since_date: datetime,
) -> List[Publication]:
    """Expand using LLM-generated query variants.

    Uses LLM to suggest:
    - MeSH terms
    - Synonyms
    - Boolean queries
    - Preprint queries

    Args:
        seed_publications: Seed papers
        run_id: Run ID
        since_date: Date filter

    Returns:
        List of publications from expanded queries
    """
    # For now, use a simple heuristic expansion (TODO: integrate LLM)
    # This is a placeholder implementation

    logger.info("LLM query expansion: generating query variants")

    # Hardcoded expanded queries for SpotItEarly mission (replace with LLM later)
    expanded_queries = [
        '("volatile organic compounds"[Title/Abstract] OR VOC[Title/Abstract]) AND cancer[Title/Abstract]',
        '("electronic nose"[Title/Abstract] OR "e-nose"[Title/Abstract]) AND (cancer[Title/Abstract] OR tumor[Title/Abstract])',
        '"breath analysis"[Title/Abstract] AND (screening[Title/Abstract] OR detection[Title/Abstract])',
        '"canine olfaction"[Title/Abstract] OR "cancer detection dog"[Title/Abstract]',
        '("GC-MS"[Title/Abstract] OR "gas chromatography"[Title/Abstract]) AND "breath"[Title/Abstract] AND cancer[Title/Abstract]',
    ]

    expanded_queries = expanded_queries[:LLM_EXPANSION_MAX_QUERIES]

    all_llm_pubs = []

    for query in expanded_queries:
        try:
            time.sleep(NCBI_POLITENESS_DELAY)

            # Search PubMed with expanded query
            params = {
                "db": "pubmed",
                "term": query,
                "retmax": LLM_EXPANSION_MAX_RESULTS_PER_QUERY,
                "retmode": "json",
                "sort": "relevance",
                "datetype": "pdat",
                "mindate": since_date.strftime("%Y/%m/%d"),
            }

            response = requests.get(ESEARCH_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            pmids = data.get("esearchresult", {}).get("idlist", [])

            if pmids:
                logger.debug("LLM query '%s': found %d PMIDs", query[:60], len(pmids))
                query_pubs = _fetch_pubmed_by_pmids(pmids, run_id, "LLM-Expansion", since_date)
                all_llm_pubs.extend(query_pubs)

        except Exception as e:
            logger.warning("LLM query expansion failed for query '%s': %s", query[:60], e)
            continue

    logger.info("LLM query expansion: collected %d papers", len(all_llm_pubs))
    return all_llm_pubs


def _expand_watchlist(
    run_id: str,
    since_date: datetime,
) -> List[Publication]:
    """Expand using entity/venue watchlist.

    Placeholder for future implementation.
    Would query PubMed for publications from:
    - KOL authors
    - Top institutions
    - Key journals
    - Relevant companies/sponsors

    Args:
        run_id: Run ID
        since_date: Date filter

    Returns:
        List of publications
    """
    # TODO: Implement watchlist system with persistent storage
    logger.info("Watchlist expansion: not yet implemented")
    return []


# Helper functions

def _get_elink_cache_key(pmid: str) -> str:
    """Generate cache key for ELink API call.

    Args:
        pmid: PubMed ID

    Returns:
        Cache key string
    """
    # Use PMID and PUBMED_NEIGHBORS_PER_SEED to ensure cache invalidation if config changes
    from config.expansion_config import PUBMED_NEIGHBORS_PER_SEED
    cache_str = f"elink_v1_{pmid}_limit{PUBMED_NEIGHBORS_PER_SEED}"
    return hashlib.md5(cache_str.encode()).hexdigest()


def _load_cached_elink(cache_key: str) -> Optional[List[str]]:
    """Load cached ELink neighbor PMIDs.

    Args:
        cache_key: Cache key

    Returns:
        List of neighbor PMIDs or None if not cached
    """
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.json")

    if not os.path.exists(cache_path):
        return None

    try:
        # Check cache age (expire after 7 days)
        cache_age_seconds = time.time() - os.path.getmtime(cache_path)
        if cache_age_seconds > 7 * 24 * 3600:
            logger.debug("Cache expired for key %s", cache_key)
            return None

        with open(cache_path, 'r') as f:
            data = json.load(f)
            return data.get("neighbor_pmids", [])
    except Exception as e:
        logger.warning("Failed to load cache for key %s: %s", cache_key, e)
        return None


def _save_cached_elink(cache_key: str, neighbor_pmids: List[str]) -> None:
    """Save ELink neighbor PMIDs to cache.

    Args:
        cache_key: Cache key
        neighbor_pmids: List of neighbor PMIDs
    """
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.json")

    try:
        data = {
            "neighbor_pmids": neighbor_pmids,
            "cached_at": time.time(),
        }
        with open(cache_path, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning("Failed to save cache for key %s: %s", cache_key, e)


def _fetch_pubmed_by_pmids(
    pmids: List[str],
    run_id: str,
    source_name: str,
    since_date: datetime,
) -> List[Publication]:
    """Fetch PubMed publications by PMIDs using ESummary."""
    if not pmids:
        return []

    try:
        time.sleep(NCBI_POLITENESS_DELAY)

        params = {
            "db": "pubmed",
            "id": ",".join(pmids[:100]),  # ESummary max ~100 IDs
            "retmode": "json",
        }

        response = requests.get(ESUMMARY_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        results = data.get("result", {})
        publications = []

        for pmid in pmids[:100]:
            if pmid not in results or pmid == "uids":
                continue

            article = results[pmid]

            title = article.get("title", "Untitled")
            if title.endswith("."):
                title = title[:-1]

            authors = []
            author_list = article.get("authors", [])
            for author in author_list:
                if isinstance(author, dict):
                    name = author.get("name", "")
                    if name:
                        authors.append(name)

            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            pub_id = compute_id(title, source_name, url)

            # Extract date (simplified)
            pub_date_str = article.get("pubdate", "")
            try:
                pub_date = datetime.strptime(pub_date_str, "%Y %b %d") if pub_date_str else since_date
            except:
                pub_date = since_date

            # Date filter
            if pub_date < since_date:
                continue

            source_info = article.get("source", "")
            fulljournalname = article.get("fulljournalname", "")
            raw_text = f"Journal: {fulljournalname or source_info}\nAuthors: {', '.join(authors) if authors else 'N/A'}"

            publication = Publication(
                id=pub_id,
                title=title,
                authors=authors,
                source=source_name,
                date=pub_date.isoformat(),
                url=url,
                raw_text=raw_text,
                summary="",
                run_id=run_id,
            )
            publications.append(publication)

        return publications

    except Exception as e:
        logger.warning("Failed to fetch PubMed by PMIDs: %s", e)
        return []


def _fetch_papers_by_ids(
    ids: List[str],
    run_id: str,
    source_name: str,
    since_date: datetime,
) -> List[Publication]:
    """Fetch papers by generic IDs (DOIs or PMIDs or API-specific IDs).

    Resolves IDs to PMIDs/DOIs and fetches metadata from PubMed.
    """
    if not ids:
        return []

    logger.debug("Fetching %d papers by IDs for source %s", len(ids), source_name)

    # Determine source API from source_name
    source_api = "unknown"
    if "openalex" in source_name.lower():
        source_api = "openalex"
    elif "semantic" in source_name.lower():
        source_api = "semantic_scholar"

    # Try to detect if IDs are already PMIDs (all numeric)
    if ids and all(str(id).isdigit() for id in ids[:5]):
        logger.debug("IDs appear to be PMIDs, fetching directly")
        return _fetch_pubmed_by_pmids(ids, run_id, source_name, since_date)

    # Resolve IDs to PMIDs/DOIs
    logger.debug("Resolving %d IDs to PMIDs/DOIs (source_api=%s)", len(ids), source_api)
    resolved = resolve_ids_to_identifiers(ids, source_api=source_api)

    # Collect PMIDs and DOIs
    pmids_to_fetch = []
    dois_to_resolve = []

    for pmid, doi in resolved:
        if pmid:
            pmids_to_fetch.append(pmid)
        elif doi:
            dois_to_resolve.append(doi)

    logger.info(
        "ID Resolution for %s: %d total IDs -> %d PMIDs, %d DOIs (need DOI->PMID lookup), %d unresolved",
        source_name,
        len(ids),
        len(pmids_to_fetch),
        len(dois_to_resolve),
        len(ids) - len(pmids_to_fetch) - len(dois_to_resolve)
    )

    # Resolve DOIs to PMIDs
    if dois_to_resolve:
        logger.debug("Resolving %d DOIs to PMIDs via PubMed ESearch", len(dois_to_resolve))
        for doi in dois_to_resolve[:20]:  # Limit to avoid excessive API calls
            pmid = resolve_doi_to_pmid(doi)
            if pmid:
                pmids_to_fetch.append(pmid)

    if not pmids_to_fetch:
        logger.info("No PMIDs resolved from %d IDs for %s", len(ids), source_name)
        return []

    # Fetch publications by PMIDs
    logger.info("Fetching %d publications from PubMed for %s", len(pmids_to_fetch), source_name)
    return _fetch_pubmed_by_pmids(pmids_to_fetch, run_id, source_name, since_date)
