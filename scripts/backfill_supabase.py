"""
One-shot backfill: read every project's raw audit log file, parse it,
and load into Supabase.

Run once after applying migrations/001_lightreach_audit_events.sql to your
Supabase project, before the next scheduled daily scrape. After this, the
daily scrape keeps the table fresh.

Usage:
    python -m scripts.backfill_supabase
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from src.parser import parse_audit_log
from src.supabase_writer import write_to_supabase


def main():
    raw_dir = Path(os.environ.get("RAW_OUTPUT_DIR", "./raw_audits"))
    if not raw_dir.exists():
        print(f"ERROR: {raw_dir} does not exist. Run the scraper first to populate raw audit logs.", file=sys.stderr)
        sys.exit(1)

    index_path = raw_dir / "_index.json"
    project_index = {}
    if index_path.exists():
        project_index = {p["project_id"]: p for p in json.loads(index_path.read_text())}

    txt_files = sorted(raw_dir.glob("*.txt"))
    if not txt_files:
        print(f"ERROR: No .txt files found in {raw_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {len(txt_files)} audit log files from {raw_dir}...")

    timelines = []
    parse_errors = []
    for txt_path in txt_files:
        project_id = txt_path.stem
        try:
            text = txt_path.read_text()
            timeline = parse_audit_log(text)
            d = timeline.to_dict()
            d["project_id"] = project_id
            if project_id in project_index and project_index[project_id].get("customer_name"):
                d["customer_name"] = project_index[project_id]["customer_name"]
            timelines.append(d)
        except Exception as e:
            parse_errors.append({"project_id": project_id, "error": str(e)})

    print(f"Parsed: {len(timelines)} successful, {len(parse_errors)} errors")
    if parse_errors:
        print("Sample errors:")
        for e in parse_errors[:3]:
            print(f"  {e}")

    if not timelines:
        print("Nothing to write. Exiting.")
        return

    print(f"\nWriting to Supabase...")
    counts = write_to_supabase(timelines)
    print(f"  Events processed: {counts['events_processed']}")
    print(f"  New events inserted (chunks): {counts['events_chunks_written']}")
    print(f"  Project snapshots upserted: {counts['snapshots_upserted']}")
    print("\nBackfill complete.")


if __name__ == "__main__":
    main()
