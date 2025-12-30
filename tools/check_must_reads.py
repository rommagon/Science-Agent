#!/usr/bin/env python3
"""Check must-reads tool output (heuristic-only and AI-enabled).

This script tests the must-reads functionality with and without AI reranking.
"""

import difflib
import json
import os
import re
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.must_reads import get_must_reads_from_db


def print_separator(title: str = ""):
    """Print a section separator."""
    if title:
        print(f"\n{'=' * 80}")
        print(f"{title:^80}")
        print(f"{'=' * 80}\n")
    else:
        print(f"{'=' * 80}\n")


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison (lowercase, remove punctuation)."""
    normalized = text.lower().strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    normalized = re.sub(r'[^\w\s-]', '', normalized)
    return normalized


def sanity_check_must_read(mr: dict) -> tuple[bool, str]:
    """Sanity check a must-read item for cross-wired LLM outputs.

    Args:
        mr: Must-read item dict

    Returns:
        Tuple of (is_valid, error_message)
    """
    # If no LLM output, skip check
    llm_score = mr['score_components'].get('llm')
    if llm_score is None or llm_score == 0:
        return True, ""

    # Check 1: Title should appear in explanation (if explanation contains a title)
    title = mr.get('title', '')
    explanation = mr.get('explanation', '')
    why = mr.get('why_it_matters', '')

    # Normalize for comparison
    title_norm = _normalize_for_comparison(title)
    explanation_norm = _normalize_for_comparison(explanation)
    why_norm = _normalize_for_comparison(why)

    # Check if title fragments appear in explanation/why (basic check)
    # Split title into words and check if substantial portion appears
    title_words = title_norm.split()
    if len(title_words) >= 3:
        # Check if at least 3 consecutive words from title appear in explanation or why
        found_in_explanation = any(
            ' '.join(title_words[i:i+3]) in explanation_norm
            for i in range(len(title_words) - 2)
        )
        found_in_why = any(
            ' '.join(title_words[i:i+3]) in why_norm
            for i in range(len(title_words) - 2)
        )

        if not found_in_explanation and not found_in_why and explanation and why:
            # Possible mismatch - LLM output doesn't reference this paper's title
            error_msg = (
                f"Possible cross-wired LLM output: title='{title[:40]}...' "
                f"not found in explanation/why"
            )
            return False, error_msg

    return True, ""


def print_must_read(mr: dict, index: int):
    """Print a single must-read entry."""
    print(f"{index}. {mr['title'][:70]}...")
    print(f"   Source: {mr['source']} ({mr['venue']})")
    print(f"   Date: {mr['published_date']}")
    print(f"   URL: {mr['url'][:60]}...")
    print(f"   Score: {mr['score_total']:.1f} (heuristic: {mr['score_components']['heuristic']:.1f}, llm: {mr['score_components'].get('llm', 'N/A')})")

    # Show tags and confidence if available
    tags = mr.get('tags', [])
    confidence = mr.get('confidence')
    if tags:
        tags_str = ', '.join(tags)
        print(f"   Tags: {tags_str}")
    if confidence:
        print(f"   Confidence: {confidence}")

    print(f"   Explanation: {mr['explanation']}")
    print(f"   Why: {mr['why_it_matters'][:100]}...")
    if mr['key_findings']:
        print(f"   Findings: {len(mr['key_findings'])} items")
    print()


def main():
    """Main function."""
    print_separator("Must Reads Tool - Heuristic Only")

    # Test 1: Heuristic-only (use_ai=False)
    print("Testing with use_ai=False (heuristic ranking only)...\n")

    result_heuristic = get_must_reads_from_db(
        since_days=30,
        limit=10,
        use_ai=False,
    )

    print(f"Generated at: {result_heuristic['generated_at']}")
    print(f"Window: {result_heuristic['window_days']} days")
    print(f"Total candidates: {result_heuristic['total_candidates']}")
    print(f"Must reads returned: {len(result_heuristic['must_reads'])}")
    print(f"Used AI: {result_heuristic['used_ai']}")
    print(f"Rerank version: {result_heuristic['rerank_version']}")
    print()

    if result_heuristic['must_reads']:
        print("Top 10 must-reads (heuristic):")
        for i, mr in enumerate(result_heuristic['must_reads'], 1):
            print_must_read(mr, i)
    else:
        print("No must-reads found.")

    print_separator("Must Reads Tool - AI Reranking (if available)")

    # Test 2: AI reranking (use_ai=True)
    print("Testing with use_ai=True (AI reranking if OPENAI_API_KEY is set)...\n")

    api_key_set = os.environ.get("OPENAI_API_KEY") is not None
    print(f"OPENAI_API_KEY: {'SET' if api_key_set else 'NOT SET'}")
    if not api_key_set:
        print("Note: AI reranking will be skipped without OPENAI_API_KEY\n")

    result_ai = get_must_reads_from_db(
        since_days=30,
        limit=10,
        use_ai=True,
        rerank_max_candidates=20,  # Use smaller candidate set for testing
    )

    print(f"Generated at: {result_ai['generated_at']}")
    print(f"Window: {result_ai['window_days']} days")
    print(f"Total candidates: {result_ai['total_candidates']}")
    print(f"Must reads returned: {len(result_ai['must_reads'])}")
    print(f"Used AI: {result_ai['used_ai']}")
    print(f"Rerank version: {result_ai['rerank_version']}")
    print()

    if result_ai['must_reads']:
        print("Top 10 must-reads (AI-reranked):")
        for i, mr in enumerate(result_ai['must_reads'], 1):
            print_must_read(mr, i)
    else:
        print("No must-reads found.")

    print_separator("Comparison")

    # Compare results
    if result_heuristic['must_reads'] and result_ai['must_reads']:
        heuristic_ids = [mr['id'] for mr in result_heuristic['must_reads']]
        ai_ids = [mr['id'] for mr in result_ai['must_reads']]

        print("Order comparison:")
        print(f"  Heuristic top 10 IDs: {heuristic_ids}")
        print(f"  AI top 10 IDs:        {ai_ids}")

        if heuristic_ids == ai_ids:
            print("\n  Rankings are IDENTICAL (AI not used or no change)")
        else:
            print("\n  Rankings DIFFER (AI reranking applied)")
            common = set(heuristic_ids) & set(ai_ids)
            print(f"  Publications in both: {len(common)}/{len(heuristic_ids)}")

    print_separator("Sanity Checks")

    # Run sanity checks on AI results
    mismatches = []
    for mr in result_ai['must_reads']:
        is_valid, error_msg = sanity_check_must_read(mr)
        if not is_valid:
            mismatches.append({
                "id": mr['id'],
                "title": mr['title'],
                "explanation": mr['explanation'],
                "why_it_matters": mr['why_it_matters'],
                "error": error_msg
            })
            print(f"❌ {error_msg}")

    if mismatches:
        print(f"\n⚠️  SANITY FAIL: {len(mismatches)} potential cross-wired LLM outputs detected")
    else:
        print("✅ SANITY PASS: All LLM outputs match their publications")

    print_separator()

    # Save full output to file
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_dir / "must_reads_heuristic.json", "w") as f:
        json.dump(result_heuristic, f, indent=2)
    print(f"Saved heuristic results to: {output_dir / 'must_reads_heuristic.json'}")

    with open(output_dir / "must_reads_ai.json", "w") as f:
        json.dump(result_ai, f, indent=2)
    print(f"Saved AI results to: {output_dir / 'must_reads_ai.json'}")

    # Save mismatches if any
    if mismatches:
        with open(output_dir / "must_reads_mismatches.json", "w") as f:
            json.dump(mismatches, f, indent=2)
        print(f"Saved mismatches to: {output_dir / 'must_reads_mismatches.json'}")

    print("\nDone!")


if __name__ == "__main__":
    main()
