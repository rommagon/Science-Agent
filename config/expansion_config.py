"""Configuration for aggressive paper expansion and scoring.

This module defines feature flags and parameters for the multi-lane
expansion system that discovers more candidates via:
- PubMed neighbor expansion (ELink)
- Bibliometrics (cited-by, references, related)
- LLM query expansion
- Entity/venue watchlist
"""

import os


# ============================================================================
# FEATURE FLAGS
# ============================================================================

# Master switch for expansion system
ENABLE_EXPANSION = os.getenv("ACITRACK_ENABLE_EXPANSION", "false").lower() in ("true", "1", "yes")

# Individual lane toggles
ENABLE_PUBMED_NEIGHBORS = os.getenv("ACITRACK_EXPANSION_PUBMED_NEIGHBORS", "true").lower() in ("true", "1", "yes")
ENABLE_BIBLIOMETRICS = os.getenv("ACITRACK_EXPANSION_BIBLIOMETRICS", "true").lower() in ("true", "1", "yes")
ENABLE_LLM_QUERIES = os.getenv("ACITRACK_EXPANSION_LLM_QUERIES", "true").lower() in ("true", "1", "yes")
ENABLE_WATCHLIST = os.getenv("ACITRACK_EXPANSION_WATCHLIST", "true").lower() in ("true", "1", "yes")

# Scoring toggles
ENABLE_RELEVANCE_SCORING = os.getenv("ACITRACK_RELEVANCE_SCORING", "true").lower() in ("true", "1", "yes")
ENABLE_CREDIBILITY_SCORING = os.getenv("ACITRACK_CREDIBILITY_SCORING", "true").lower() in ("true", "1", "yes")


# ============================================================================
# EXPANSION LANE PARAMETERS
# ============================================================================

# PubMed neighbor expansion (ELink)
PUBMED_NEIGHBORS_TOP_K = int(os.getenv("ACITRACK_PUBMED_NEIGHBORS_TOP_K", "20"))
PUBMED_NEIGHBORS_PER_SEED = int(os.getenv("ACITRACK_PUBMED_NEIGHBORS_PER_SEED", "10"))

# Bibliometrics expansion
BIBLIO_MAX_CITED_BY_PER_SEED = int(os.getenv("ACITRACK_BIBLIO_MAX_CITED_BY", "20"))
BIBLIO_MAX_REFERENCES_PER_SEED = int(os.getenv("ACITRACK_BIBLIO_MAX_REFERENCES", "20"))
BIBLIO_MAX_RELATED_PER_SEED = int(os.getenv("ACITRACK_BIBLIO_MAX_RELATED", "10"))

# LLM query expansion
LLM_EXPANSION_MAX_QUERIES = int(os.getenv("ACITRACK_LLM_MAX_QUERIES", "5"))
LLM_EXPANSION_MAX_RESULTS_PER_QUERY = int(os.getenv("ACITRACK_LLM_MAX_RESULTS_PER_QUERY", "30"))

# Entity watchlist
WATCHLIST_MAX_QUERIES_PER_DAY = int(os.getenv("ACITRACK_WATCHLIST_MAX_QUERIES", "50"))
WATCHLIST_PROMOTION_THRESHOLD = int(os.getenv("ACITRACK_WATCHLIST_PROMOTION_THRESHOLD", "3"))


# ============================================================================
# COST CONTROL (Two-Stage Filtering)
# ============================================================================

# Stage 1: Cheap heuristic filter (keep top N after relevance scoring)
STAGE1_TOP_K = int(os.getenv("ACITRACK_STAGE1_TOP_K", "200"))

# Stage 2: Expensive LLM extraction (process top M)
STAGE2_TOP_M = int(os.getenv("ACITRACK_STAGE2_TOP_M", "50"))


# ============================================================================
# BIBLIOMETRICS API CONFIGURATION
# ============================================================================

# Paid provider (if available)
BIBLIO_API_KEY = os.getenv("ACITRACK_BIBLIO_API_KEY", "")
BIBLIO_PROVIDER = os.getenv("ACITRACK_BIBLIO_PROVIDER", "").lower()  # "dimensions" | "scopus" | "wos" | ""

# Free fallbacks (no API key needed)
BIBLIO_USE_OPENALEX = os.getenv("ACITRACK_BIBLIO_USE_OPENALEX", "true").lower() in ("true", "1", "yes")
BIBLIO_USE_SEMANTIC_SCHOLAR = os.getenv("ACITRACK_BIBLIO_USE_SEMANTIC_SCHOLAR", "true").lower() in ("true", "1", "yes")
BIBLIO_USE_CROSSREF = os.getenv("ACITRACK_BIBLIO_USE_CROSSREF", "true").lower() in ("true", "1", "yes")

# Rate limiting (requests per second)
BIBLIO_RATE_LIMIT_RPS = float(os.getenv("ACITRACK_BIBLIO_RATE_LIMIT_RPS", "0.5"))  # Conservative default


# ============================================================================
# RELEVANCE KEYWORDS (SpotItEarly Mission-Critical)
# ============================================================================

# High-value keywords (50 points each)
RELEVANCE_KEYWORDS_HIGH = [
    "canine olfaction",
    "cancer detection dog",
    "cancer-sniffing dog",
    "volatile organic compound",
    "VOC",
    "breath VOC",
    "breathomics",
    "breath analysis",
    "exhaled breath",
    "electronic nose",
    "e-nose",
    "sensor array",
    "GC-MS",
    "gas chromatography",
    "mass spectrometry",
]

# Medium-value keywords (25 points each)
RELEVANCE_KEYWORDS_MEDIUM = [
    "early detection",
    "screening",
    "biomarker",
    "liquid biopsy",
    "metabolomic",
    "cancer diagnosis",
    "sensitivity",
    "specificity",
    "AUC",
    "receiver operating characteristic",
    "double-blind",
    "clinical trial",
]

# Low-value keywords (10 points each)
RELEVANCE_KEYWORDS_LOW = [
    "cancer",
    "oncology",
    "tumor",
    "malignancy",
    "metastasis",
]

# Negative keywords (penalize non-relevant papers)
RELEVANCE_KEYWORDS_NEGATIVE = [
    "treatment",
    "chemotherapy",
    "radiation therapy",
    "surgery",
    "immunotherapy",
    "targeted therapy",
]


# ============================================================================
# CREDIBILITY SCORING WEIGHTS
# ============================================================================

# Credibility score is a weighted composite (0-100)
CREDIBILITY_WEIGHT_CITATIONS = 0.25  # Citation count (time-normalized if available)
CREDIBILITY_WEIGHT_VENUE = 0.20  # Journal/venue ranking
CREDIBILITY_WEIGHT_PUB_TYPE = 0.15  # Publication type (RCT > meta-analysis > observational > preprint)
CREDIBILITY_WEIGHT_SAMPLE_SIZE = 0.15  # Sample size (extracted from abstract)
CREDIBILITY_WEIGHT_METRICS = 0.15  # Strength of reported metrics (AUC/sensitivity/specificity with CI)
CREDIBILITY_WEIGHT_AUTHOR = 0.05  # KOL/institution signal
CREDIBILITY_WEIGHT_SPONSOR = 0.05  # Corporate tie flag (negative signal)


# ============================================================================
# WATCHLIST PERSISTENCE
# ============================================================================

WATCHLIST_DB_PATH = os.getenv("ACITRACK_WATCHLIST_DB_PATH", "data/db/watchlist.db")
WATCHLIST_JSON_PATH = os.getenv("ACITRACK_WATCHLIST_JSON_PATH", "data/watchlist/entities.json")
