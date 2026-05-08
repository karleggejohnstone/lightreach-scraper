"""
Main entry point for the daily scrape.
Run: python -m src.main                # full scrape + parse + write
     python -m src.main --from-cache   # parse existing raw_audits/ + write (no scrape)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from src.parser import parse_audit_log
from src.scraper import run as run_scraper, RAW_OUTPUT_DIR
from src.sheets import write_funding_report

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main(from_cache: bool = False):
    spreadsheet_id = os.environ.get("FUNDING_REPORT_SHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("FUNDING_REPORT_SHEET_ID env var not set")

    if from_cache:
        index_path = RAW_OUTPUT_DIR / "_index.json"
        if not index_path.exists():
            raise RuntimeError(f"{index_path} not found — run a scrape first.")
        print(f"=== Step 1: Loading cached scrape from {index_path} ===")
        projects = json.loads(index_path.read_text())
        print(f"  Loaded {len(projects)} projects from cache")
    else:
        print("=== Step 1: Scrape portal ===")
        urls_env = os.environ.get("PROJECT_URLS", "").strip()
        project_urls = [u.strip() for u in urls_env.split(",") if u.strip()] or None
        projects = run_scraper(headless=True, project_urls=project_urls)

    print(f"\n=== Step 2: Parse {len(projects)} audit logs ===")
    timelines = []
    for proj in projects:
        if "raw_path" not in proj:
            continue
        try:
            text = Path(proj["raw_path"]).read_text()
            timeline = parse_audit_log(text)
            d = timeline.to_dict()
            d["project_id"] = proj["project_id"]
            d["customer_name"] = proj.get("customer_name") or d.get("customer_name")
            timelines.append(d)
        except Exception as e:
            print(f"  Parse error for {proj['project_id']}: {e}", file=sys.stderr)
    print(f"  Parsed {len(timelines)} timelines")

    print(f"\n=== Step 3: Write to Google Sheets ===")
    write_funding_report(timelines, spreadsheet_id)

    print("\n=== Done ===")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--from-cache",
        action="store_true",
        help="Skip scraping; parse existing raw_audits/_index.json and write to Sheets.",
    )
    args = p.parse_args()
    main(from_cache=args.from_cache)
