"""Two-stage gating for tri-model pipeline.

This module implements a cheap, high-recall gate to filter publications before
expensive tri-model evaluation (Claude + Gemini + GPT). The gate optimizes for
recall (not missing must-reads) over precision.

Gate buckets:
- high: Strong signal, always evaluate
- maybe: Moderate signal, always evaluate
- low: Weak signal, only audit sample evaluated

Publications are promoted to higher buckets via:
1. Venue whitelist (prestigious journals always pass)
2. Keyword matching (early detection, screening, biomarkers, etc.)
3. Title/abstract content analysis
"""

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class GateBucket(str, Enum):
    """Gate bucket classification."""
    HIGH = "high"
    MAYBE = "maybe"
    LOW = "low"


@dataclass
class GateResult:
    """Result of gating a single publication."""
    bucket: GateBucket
    score: int  # 0-100
    reason: str
    venue_match: bool
    keyword_matches: List[str]
    audit_selected: bool = False  # True if low bucket but selected for audit

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "gate_bucket": self.bucket.value,
            "gate_score": self.score,
            "gate_reason": self.reason,
            "gate_venue_match": self.venue_match,
            "gate_keyword_matches": self.keyword_matches,
            "gate_audit_selected": self.audit_selected,
        }


@dataclass
class GatingStats:
    """Statistics from gating a batch of publications."""
    total: int
    high_count: int
    maybe_count: int
    low_count: int
    audited_low_count: int
    venue_promoted_count: int
    keyword_promoted_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total": self.total,
            "high": self.high_count,
            "maybe": self.maybe_count,
            "low": self.low_count,
            "audited_low": self.audited_low_count,
            "venue_promoted": self.venue_promoted_count,
            "keyword_promoted": self.keyword_promoted_count,
            "to_evaluate": self.high_count + self.maybe_count + self.audited_low_count,
        }


# Default venue whitelist - high-impact journals for early detection research
DEFAULT_VENUE_WHITELIST = [
    # Top medical journals
    "nejm", "new england journal of medicine",
    "lancet", "the lancet",
    "jama", "journal of the american medical association",
    "bmj", "british medical journal",
    "annals of internal medicine",

    # Top science journals
    "nature", "nature medicine", "nature cancer", "nature communications",
    "science", "science translational medicine",
    "cell", "cell reports medicine", "cancer cell",

    # Cancer-specific high-impact
    "cancer discovery", "journal of clinical oncology", "jco",
    "clinical cancer research", "cancer research",
    "annals of oncology", "lancet oncology",
    "jama oncology", "jama network open",

    # Screening/detection focused
    "cancer epidemiology biomarkers prevention",
    "cancer prevention research",
    "international journal of cancer",
    "gut", "gastroenterology",

    # Preprint servers (for cutting-edge research)
    "biorxiv", "medrxiv", "arxiv",
]

# Default keywords - high-recall set for early detection
DEFAULT_KEYWORDS = [
    # Core early detection terms
    "early detection", "early diagnosis", "early-stage",
    "screening", "cancer screening", "population screening",
    "early cancer", "precancerous", "premalignant",

    # Biomarkers and liquid biopsy
    "biomarker", "biomarkers", "tumor marker", "tumour marker",
    "liquid biopsy", "liquid biopsies",
    "ctdna", "cfdna", "circulating tumor dna", "circulating tumour dna",
    "circulating free dna", "cell-free dna", "cell free dna",
    "circulating tumor cells", "ctc", "ctcs",

    # Methylation and epigenetics
    "methylation", "dna methylation", "epigenetic",
    "methylation biomarker", "methylation signature",

    # Multi-cancer detection
    "mced", "multi-cancer", "multicancer", "pan-cancer",
    "multi-cancer early detection", "galleri",

    # Non-invasive detection modalities
    "urine", "urinary", "urine-based",
    "stool", "fecal", "faecal", "stool-based",
    "breath", "exhaled breath", "breath analysis",
    "voc", "volatile organic compounds",
    "saliva", "salivary",

    # Novel detection approaches
    "canine detection", "cancer-sniffing dogs", "olfactory",
    "electronic nose", "e-nose",
    "ai-assisted", "machine learning", "deep learning",

    # Specific test types
    "colonoscopy", "mammography", "mammogram",
    "low-dose ct", "ldct", "lung cancer screening",
    "psa", "prostate-specific antigen",
    "ca-125", "ca125", "ovarian cancer screening",
    "cologuard", "fit test", "fobt",

    # Clinical validation terms
    "sensitivity", "specificity", "auc", "roc",
    "positive predictive value", "ppv",
    "negative predictive value", "npv",
    "validation cohort", "validation study",
    "prospective", "prospective study", "prospective cohort",

    # Cancer types of high interest
    "pancreatic cancer", "pancreatic", "pdac",
    "ovarian cancer", "ovarian",
    "lung cancer", "nsclc", "sclc",
    "colorectal cancer", "colon cancer", "crc",
    "liver cancer", "hepatocellular", "hcc",
]

# Negative keywords that suggest low relevance
NEGATIVE_KEYWORDS = [
    "treatment", "therapy", "therapeutic", "chemotherapy",
    "surgery", "surgical", "resection",
    "metastatic", "advanced stage", "stage iv", "stage 4",
    "palliative", "end-stage", "terminal",
    "in vitro", "cell line", "cell lines", "mouse model", "mice",
    "retrospective review", "case report", "case series",
    "editorial", "commentary", "letter to editor", "correspondence",
    "erratum", "correction", "retraction",
]


def _compute_list_hash(items: List[str]) -> str:
    """Compute SHA256 hash of a sorted list of strings."""
    normalized = sorted([s.lower().strip() for s in items])
    content = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _load_list_from_file(path: str) -> List[str]:
    """Load a list from JSON or YAML file."""
    path_obj = Path(path)
    if not path_obj.exists():
        raise FileNotFoundError(f"List file not found: {path}")

    content = path_obj.read_text()

    if path.endswith(".json"):
        return json.loads(content)
    elif path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(content)
    else:
        # Assume one item per line
        return [line.strip() for line in content.splitlines() if line.strip()]


def _normalize_text(text: str) -> str:
    """Normalize text for matching (lowercase, collapse whitespace)."""
    if not text:
        return ""
    # Lowercase and collapse whitespace
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    return normalized


def _extract_venue(pub: Dict[str, Any]) -> str:
    """Extract venue/journal name from publication."""
    # Try multiple fields
    venue = pub.get("venue") or pub.get("source") or pub.get("journal") or ""
    return _normalize_text(venue)


def _match_keywords(text: str, keywords: List[str]) -> List[str]:
    """Find all keywords that match in text."""
    if not text:
        return []

    text_lower = _normalize_text(text)
    matches = []

    for keyword in keywords:
        keyword_lower = keyword.lower().strip()
        # Use word boundary matching for short keywords, substring for longer ones
        if len(keyword_lower) <= 4:
            # Short keywords: require word boundaries
            pattern = r'\b' + re.escape(keyword_lower) + r'\b'
            if re.search(pattern, text_lower):
                matches.append(keyword)
        else:
            # Longer keywords: substring match is fine
            if keyword_lower in text_lower:
                matches.append(keyword)

    return matches


def _count_negative_matches(text: str) -> int:
    """Count negative keyword matches in text."""
    if not text:
        return 0

    text_lower = _normalize_text(text)
    count = 0

    for keyword in NEGATIVE_KEYWORDS:
        if keyword.lower() in text_lower:
            count += 1

    return count


def gate_publication(
    pub: Dict[str, Any],
    venue_whitelist: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
) -> GateResult:
    """Gate a single publication.

    Args:
        pub: Publication dict with title, raw_text/abstract, source/venue
        venue_whitelist: List of whitelisted venue names (case-insensitive)
        keywords: List of keywords to match (case-insensitive)

    Returns:
        GateResult with bucket, score, and reason
    """
    if venue_whitelist is None:
        venue_whitelist = DEFAULT_VENUE_WHITELIST
    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    # Extract text fields
    title = pub.get("title", "")
    abstract = pub.get("raw_text") or pub.get("abstract") or ""
    combined_text = f"{title} {abstract}"
    venue = _extract_venue(pub)

    # Initialize scoring
    score = 0
    reasons = []
    venue_match = False
    keyword_matches = []

    # Check venue whitelist
    for whitelisted in venue_whitelist:
        whitelisted_lower = whitelisted.lower()
        if whitelisted_lower in venue:
            venue_match = True
            score += 40
            reasons.append(f"venue:{whitelisted}")
            break

    # Check keywords in title (higher weight)
    title_keywords = _match_keywords(title, keywords)
    if title_keywords:
        keyword_matches.extend(title_keywords)
        score += min(len(title_keywords) * 15, 45)  # Cap at 45
        reasons.append(f"title_kw:{len(title_keywords)}")

    # Check keywords in abstract
    abstract_keywords = _match_keywords(abstract, keywords)
    # Only add keywords not already matched in title
    new_abstract_keywords = [k for k in abstract_keywords if k not in title_keywords]
    if new_abstract_keywords:
        keyword_matches.extend(new_abstract_keywords)
        score += min(len(new_abstract_keywords) * 5, 25)  # Cap at 25
        reasons.append(f"abstract_kw:{len(new_abstract_keywords)}")

    # Penalize negative keywords
    negative_count = _count_negative_matches(combined_text)
    if negative_count > 0:
        penalty = min(negative_count * 5, 20)  # Cap penalty at 20
        score = max(0, score - penalty)
        reasons.append(f"neg_kw:-{penalty}")

    # Boost for multiple strong signals
    if venue_match and len(keyword_matches) >= 3:
        score += 10
        reasons.append("multi_signal")

    # Clamp score
    score = max(0, min(100, score))

    # Determine bucket
    if score >= 50:
        bucket = GateBucket.HIGH
    elif score >= 25:
        bucket = GateBucket.MAYBE
    else:
        bucket = GateBucket.LOW

    # Safety net: force promotion for very strong signals
    # These are high-recall keywords that should NEVER be in LOW bucket
    strong_keywords = [
        # Core detection terms
        "multi-cancer early detection", "mced", "liquid biopsy",
        "cancer screening", "early detection", "screening study",
        "prospective screening", "population screening",
        # Biomarker types
        "ctdna", "cfdna", "circulating tumor dna", "circulating free dna",
        "cell-free dna", "cell free dna",
        # Novel detection modalities
        "canine detection", "dog detection", "trained dogs",
        "breath analysis", "exhaled breath", "volatile organic",
        "electronic nose", "e-nose",
        # Specific test mentions
        "biomarker validation", "diagnostic accuracy",
    ]
    for strong_kw in strong_keywords:
        if strong_kw.lower() in _normalize_text(combined_text):
            if bucket == GateBucket.LOW:
                bucket = GateBucket.MAYBE
                reasons.append(f"safety_net:{strong_kw}")
                break

    # Force high for venue + any keyword
    if venue_match and keyword_matches and bucket != GateBucket.HIGH:
        bucket = GateBucket.HIGH
        reasons.append("venue+kw_promotion")

    reason = "; ".join(reasons) if reasons else "no_signals"

    return GateResult(
        bucket=bucket,
        score=score,
        reason=reason,
        venue_match=venue_match,
        keyword_matches=keyword_matches[:10],  # Limit for storage
        audit_selected=False,
    )


def gate_publications(
    publications: List[Dict[str, Any]],
    venue_whitelist: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
    audit_rate: float = 0.02,
    audit_seed: Optional[int] = None,
) -> Tuple[List[Tuple[Dict[str, Any], GateResult]], GatingStats]:
    """Gate a batch of publications.

    Args:
        publications: List of publication dicts
        venue_whitelist: Optional venue whitelist (uses default if None)
        keywords: Optional keyword list (uses default if None)
        audit_rate: Fraction of LOW bucket to audit (default 2%)
        audit_seed: Random seed for deterministic audit sampling

    Returns:
        Tuple of:
        - List of (publication, GateResult) tuples
        - GatingStats with bucket counts
    """
    if venue_whitelist is None:
        venue_whitelist = DEFAULT_VENUE_WHITELIST
    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    results = []
    high_count = 0
    maybe_count = 0
    low_count = 0
    venue_promoted = 0
    keyword_promoted = 0

    # First pass: gate all publications
    for pub in publications:
        result = gate_publication(pub, venue_whitelist, keywords)
        results.append((pub, result))

        if result.bucket == GateBucket.HIGH:
            high_count += 1
        elif result.bucket == GateBucket.MAYBE:
            maybe_count += 1
        else:
            low_count += 1

        if result.venue_match:
            venue_promoted += 1
        if result.keyword_matches:
            keyword_promoted += 1

    # Second pass: select audit sample from LOW bucket
    low_indices = [i for i, (_, r) in enumerate(results) if r.bucket == GateBucket.LOW]

    if low_indices and audit_rate > 0:
        rng = random.Random(audit_seed)
        n_audit = max(1, int(len(low_indices) * audit_rate))
        n_audit = min(n_audit, len(low_indices))  # Don't exceed available

        audit_indices = set(rng.sample(low_indices, n_audit))

        for idx in audit_indices:
            pub, result = results[idx]
            # Create new result with audit_selected=True
            results[idx] = (pub, GateResult(
                bucket=result.bucket,
                score=result.score,
                reason=result.reason,
                venue_match=result.venue_match,
                keyword_matches=result.keyword_matches,
                audit_selected=True,
            ))

    audited_low_count = sum(1 for _, r in results if r.audit_selected)

    stats = GatingStats(
        total=len(publications),
        high_count=high_count,
        maybe_count=maybe_count,
        low_count=low_count,
        audited_low_count=audited_low_count,
        venue_promoted_count=venue_promoted,
        keyword_promoted_count=keyword_promoted,
    )

    logger.info(
        "Gating complete: %d total → %d high, %d maybe, %d low (%d audited) → %d to evaluate",
        stats.total,
        stats.high_count,
        stats.maybe_count,
        stats.low_count,
        stats.audited_low_count,
        stats.high_count + stats.maybe_count + stats.audited_low_count,
    )

    return results, stats


def filter_for_evaluation(
    gated_results: List[Tuple[Dict[str, Any], GateResult]],
) -> List[Tuple[Dict[str, Any], GateResult]]:
    """Filter gated publications to those that should be tri-model evaluated.

    Returns publications in HIGH, MAYBE, or audit-selected LOW buckets.
    """
    return [
        (pub, result)
        for pub, result in gated_results
        if result.bucket in (GateBucket.HIGH, GateBucket.MAYBE) or result.audit_selected
    ]


def get_gating_config_hashes(
    venue_whitelist: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Get hashes of gating configuration lists."""
    venues = venue_whitelist or DEFAULT_VENUE_WHITELIST
    kws = keywords or DEFAULT_KEYWORDS

    return {
        "venue_whitelist_hash": _compute_list_hash(venues),
        "keywords_hash": _compute_list_hash(kws),
        "venue_count": len(venues),
        "keyword_count": len(kws),
    }


def load_gating_config(
    venue_whitelist_path: Optional[str] = None,
    keywords_path: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Load gating configuration from files or return defaults.

    Args:
        venue_whitelist_path: Path to venue whitelist file (json/yaml/txt)
        keywords_path: Path to keywords file (json/yaml/txt)

    Returns:
        Tuple of (venue_whitelist, keywords)
    """
    if venue_whitelist_path:
        venues = _load_list_from_file(venue_whitelist_path)
        logger.info("Loaded %d venues from %s", len(venues), venue_whitelist_path)
    else:
        venues = DEFAULT_VENUE_WHITELIST
        logger.info("Using default venue whitelist (%d venues)", len(venues))

    if keywords_path:
        keywords = _load_list_from_file(keywords_path)
        logger.info("Loaded %d keywords from %s", len(keywords), keywords_path)
    else:
        keywords = DEFAULT_KEYWORDS
        logger.info("Using default keyword list (%d keywords)", len(keywords))

    return venues, keywords
