#!/usr/bin/env python3
"""Check must-reads tool output (heuristic-only and AI-enabled).

This script tests the must-reads functionality with and without AI reranking.
"""

import json
import os
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

    print("\nDone!")


if __name__ == "__main__":
    main()
