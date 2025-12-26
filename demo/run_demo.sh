#!/bin/bash
# AciTrack V1 Demo Runner
# Runs a quick demo with a small dataset to showcase the tracker

set -e

echo "========================================================================"
echo "AciTrack V1 Demo"
echo "========================================================================"
echo ""
echo "This demo will:"
echo "  1. Reset the snapshot (mark all items as NEW)"
echo "  2. Fetch publications from the last 7 days"
echo "  3. Limit output to 5 items per source"
echo "  4. Generate reports and outputs"
echo ""
echo "Press Enter to continue or Ctrl+C to cancel..."
read

# Navigate to project root (one level up from demo/)
cd "$(dirname "$0")/.."

# Run the demo command
echo ""
echo "Running: python run.py --reset-snapshot --since-days 7 --max-items-per-source 5"
echo ""

python3 run.py --reset-snapshot --since-days 7 --max-items-per-source 5

echo ""
echo "========================================================================"
echo "Demo Complete!"
echo "========================================================================"
echo ""
echo "Output files have been generated. View them in this order:"
echo ""
echo "  1. data/output/latest_report.md"
echo "     → Full markdown report with NEW publications organized by source"
echo ""
echo "  2. data/output/latest_new.csv"
echo "     → CSV export of NEW publications with commercial signals"
echo ""
echo "  3. data/output/latest_manifest.json"
echo "     → Run provenance and metadata"
echo ""
echo "To understand what you're seeing, read: demo/DEMO_GUIDE.md"
echo ""
echo "Tip: Run 'python run.py' again without --reset-snapshot to see"
echo "     change detection in action (should show 0 NEW items)."
echo ""
