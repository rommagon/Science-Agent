#!/usr/bin/env python3
"""Quality check for must-reads output - flag irrelevant publications.

This script helps identify potentially irrelevant publications in the must-reads
output by checking for off-topic indicators like plant biology, animal ecology, etc.
Also checks for cross-wired LLM outputs (validation mismatches).

Exit codes:
  0 - All checks passed
  1 - Mismatches or quality issues detected
"""

import difflib
import json
import re
import sys
from pathlib import Path
from typing import List, Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.must_reads import get_must_reads_from_db


# Off-topic keywords that indicate irrelevant publications
IRRELEVANT_INDICATORS = {
    "plant biology": ["plant", "arabidopsis", "photosynthesis", "chloroplast", "phytochrome"],
    "animal ecology": ["zebrafish", "drosophila", "c. elegans", "mouse model (non-cancer)", "rat model (non-cancer)"],
    "veterinary": ["canine", "feline", "bovine", "equine", "veterinary"],
    "non-cancer medical": ["cardiology", "neurology (non-tumor)", "diabetes", "hypertension"],
    "methodology only": ["bioinformatics tool", "statistical method", "database", "software"],
}

# Cancer-relevant keywords that should NOT trigger flags
CANCER_RELEVANT = [
    "cancer", "tumor", "carcinoma", "oncology", "malignancy", "metastasis",
    "ctDNA", "liquid biopsy", "biomarker", "screening", "detection",
    "biopsy", "methylation", "mutation", "genomic", "sequencing"
]


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison (lowercase, remove punctuation)."""
    normalized = text.lower().strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    normalized = re.sub(r'[^\w\s-]', '', normalized)
    return normalized


def check_cross_wiring(pub: Dict) -> List[str]:
    """Check for cross-wired LLM outputs (title mismatch with explanation).

    Args:
        pub: Publication dict with title, explanation, why_it_matters

    Returns:
        List of warning messages (empty if no issues)
    """
    warnings = []

    # If no LLM output, skip check
    llm_score = pub.get('score_components', {}).get('llm')
    if llm_score is None or llm_score == 0:
        return warnings

    title = pub.get('title', '')
    explanation = pub.get('explanation', '')
    why = pub.get('why_it_matters', '')

    # Normalize for comparison
    title_norm = _normalize_for_comparison(title)
    explanation_norm = _normalize_for_comparison(explanation)
    why_norm = _normalize_for_comparison(why)

    # Check if title fragments appear in explanation/why
    title_words = title_norm.split()
    if len(title_words) >= 3:
        # Check if at least 3 consecutive words from title appear
        found_in_explanation = any(
            ' '.join(title_words[i:i+3]) in explanation_norm
            for i in range(len(title_words) - 2)
        )
        found_in_why = any(
            ' '.join(title_words[i:i+3]) in why_norm
            for i in range(len(title_words) - 2)
        )

        if not found_in_explanation and not found_in_why and explanation and why:
            warnings.append(f"Cross-wired LLM output: title not found in explanation/why")

    return warnings


def check_irrelevance(pub: Dict) -> List[str]:
    """Check if a publication has off-topic indicators.

    Args:
        pub: Publication dict with title, venue, summary, tags

    Returns:
        List of warning messages (empty if no issues)
    """
    warnings = []

    # Combine text to search
    text_to_check = " ".join([
        pub.get("title", "").lower(),
        pub.get("venue", "").lower(),
        pub.get("summary", "").lower(),
    ])

    # Skip if clearly cancer-relevant
    is_cancer_relevant = any(keyword in text_to_check for keyword in CANCER_RELEVANT)

    # Check for irrelevant indicators
    for category, keywords in IRRELEVANT_INDICATORS.items():
        for keyword in keywords:
            if keyword in text_to_check and not is_cancer_relevant:
                warnings.append(f"Possible {category}: '{keyword}' found in text")
                break  # Only one warning per category

    return warnings


def print_separator(title: str = ""):
    """Print a section separator."""
    if title:
        print(f"\n{'=' * 80}")
        print(f"{title:^80}")
        print(f"{'=' * 80}\n")
    else:
        print(f"{'=' * 80}\n")


def main():
    """Main function."""
    print_separator("Must Reads Quality Check")

    print("Fetching must-reads with AI reranking (if available)...\n")

    # Fetch must-reads with AI reranking
    result = get_must_reads_from_db(
        since_days=30,
        limit=20,  # Check top 20
        use_ai=True,
        rerank_max_candidates=50,
    )

    print(f"Generated at: {result['generated_at']}")
    print(f"Total candidates: {result['total_candidates']}")
    print(f"Must reads returned: {len(result['must_reads'])}")
    print(f"Used AI: {result['used_ai']}")
    print(f"Rerank version: {result['rerank_version']}")
    print()

    if not result['must_reads']:
        print("No must-reads found. Nothing to check.")
        return

    print_separator("Quality Checks")

    # Check each publication
    flagged_count = 0
    low_score_count = 0
    cross_wired_count = 0

    for i, mr in enumerate(result['must_reads'], 1):
        warnings = []

        # Check for cross-wired LLM outputs (CRITICAL)
        cross_wire_warnings = check_cross_wiring(mr)
        if cross_wire_warnings:
            cross_wired_count += 1
            warnings.extend(cross_wire_warnings)

        # Check for irrelevance
        irrelevance_warnings = check_irrelevance(mr)
        warnings.extend(irrelevance_warnings)

        # Also flag low LLM scores (< 10 means "irrelevant" per rubric)
        llm_score = mr['score_components'].get('llm')
        if llm_score is not None and llm_score > 0 and llm_score < 10:
            warnings.append(f"Low LLM score: {llm_score}/100 (threshold: 10)")
            low_score_count += 1

        if warnings:
            flagged_count += 1
            print(f"🚩 FLAGGED #{i}: {mr['title'][:60]}...")
            print(f"   ID: {mr['id']}")
            print(f"   Score: {mr['score_total']:.1f} (heuristic: {mr['score_components']['heuristic']:.1f}, llm: {llm_score})")
            print(f"   Venue: {mr['venue']}")

            # Show tags and confidence
            tags = mr.get('tags', [])
            confidence = mr.get('confidence')
            if tags:
                print(f"   Tags: {', '.join(tags)}")
            if confidence:
                print(f"   Confidence: {confidence}")

            for warning in warnings:
                print(f"   ⚠️  {warning}")
            print()

    print_separator("Summary")

    print(f"Total publications checked: {len(result['must_reads'])}")
    print(f"Cross-wired LLM outputs (CRITICAL): {cross_wired_count}")
    print(f"Flagged as potentially irrelevant: {flagged_count}")
    print(f"Low LLM scores (< 10): {low_score_count}")

    exit_code = 0

    if cross_wired_count > 0:
        print(f"\n❌ CRITICAL: {cross_wired_count} cross-wired LLM output(s) detected!")
        print("   This indicates LLM responses were attached to wrong publications.")
        print("   Review validation logic in mcp_server/ai_reranker.py")
        exit_code = 1
    elif flagged_count == 0:
        print("\n✅ No quality issues detected!")
    else:
        print(f"\n⚠️  {flagged_count} publication(s) need review")
        print("\nNext steps:")
        print("  1. Review flagged publications above")
        print("  2. If truly irrelevant, update LLM prompt or heuristics")
        print("  3. Consider adjusting keyword filters or scoring rubric")

    print_separator()

    # Save flagged results
    if flagged_count > 0:
        flagged_pubs = []
        for mr in result['must_reads']:
            warnings = []

            # Check for cross-wiring
            cross_wire_warnings = check_cross_wiring(mr)
            warnings.extend(cross_wire_warnings)

            # Check irrelevance
            irrelevance_warnings = check_irrelevance(mr)
            warnings.extend(irrelevance_warnings)

            # Check low scores
            llm_score = mr['score_components'].get('llm')
            if llm_score is not None and llm_score > 0 and llm_score < 10:
                warnings.append(f"Low LLM score: {llm_score}/100")

            if warnings:
                flagged_pubs.append({
                    "id": mr["id"],
                    "title": mr["title"],
                    "venue": mr["venue"],
                    "score_total": mr["score_total"],
                    "score_components": mr["score_components"],
                    "tags": mr.get("tags", []),
                    "confidence": mr.get("confidence"),
                    "warnings": warnings,
                })

        output_path = Path("data/output/must_reads_flagged.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(flagged_pubs, f, indent=2)
        print(f"Saved flagged publications to: {output_path}")

    print("\nDone!")

    # Exit with appropriate code
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
