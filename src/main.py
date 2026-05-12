"""
Main entry point for the daily scrape.
Run: python -m src.main

Pipeline: scrape → parse → write to Sheets → write to Supabase.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from src.alerts import send_alert, alert_on_exception, AlertError
from src.parser import parse_audit_log
from src.scraper import run as run_scraper
from src.sheets import write_funding_report
from src.supabase_writer import write_to_supabase


def main():
    spreadsheet_id = os.environ.get("FUNDING_REPORT_SHEET_ID")
    if not spreadsheet_id:
        send_alert("scrape_error", "FUNDING_REPORT_SHEET_ID env var not set")
        raise RuntimeError("FUNDING_REPORT_SHEET_ID env var not set")

    print("=== Step 1: Scrape portal ===")
    projects = run_scraper(headless=True)

    if not projects:
        send_alert(
            "zero_projects",
            "Scraper returned 0 projects from the list page",
            details={"hint": "Check session.json validity and project list selectors in scraper.py"},
        )
        raise AlertError("Zero projects collected")

    print(f"\n=== Step 2: Parse {len(projects)} audit logs ===")
    timelines = []
    parse_errors = []
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
            parse_errors.append({"project_id": proj.get("project_id"), "error": str(e)})
            print(f"  Parse error for {proj.get('project_id')}: {e}", file=sys.stderr)

    if parse_errors and len(parse_errors) > max(3, len(projects) * 0.1):
        send_alert(
            "parser_warning",
            f"{len(parse_errors)} of {len(projects)} projects failed to parse (>10%)",
            details={"sample_errors": parse_errors[:5]},
        )

    print(f"\n=== Step 3: Write to Google Sheets ===")
    try:
        write_funding_report(timelines, spreadsheet_id)
    except Exception:
        alert_on_exception("sheet_write_failed", "Google Sheets write failed")
        raise

    print(f"\n=== Step 4: Write to Supabase ===")
    try:
        counts = write_to_supabase(timelines)
        print(f"  Events processed: {counts['events_processed']}")
        print(f"  New events inserted: {counts['events_inserted_new']}")
        print(f"  Snapshot rows upserted: {counts['snapshots_upserted']}")
    except Exception:
        alert_on_exception("scrape_error", "Supabase write failed (Sheets succeeded)")

    print("\n=== Done ===")


if __name__ == "__main__":
    try:
        main()
    except AlertError:
        sys.exit(1)
    except Exception:
        alert_on_exception("scrape_error", "Unexpected error in scraper run")
        raise
