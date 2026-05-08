"""
Writes parsed project data to Google Sheets.

Auth: service account JSON. Set GOOGLE_SERVICE_ACCOUNT_JSON env var to either:
- A path to the JSON file, OR
- The raw JSON content (useful for Railway secrets)

The target spreadsheet must be shared with the service account's email
(found in the JSON under client_email) with Editor permission.

Three tabs are managed:
  1. "Funding Report" - one row per project, current state (overwritten daily)
  2. "Stipulations" - one row per stipulation event (active and historical)
  3. "Audit Events" - flat event log, one row per event (newest at top)
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _load_credentials():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var not set")

    if raw.strip().startswith("{"):
        info = json.loads(raw)
    else:
        info = json.loads(Path(raw).read_text())

    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _get_or_create_worksheet(spreadsheet, title, headers):
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=max(26, len(headers)))
        ws.append_row(headers)
    return ws


def _flatten_milestones(milestones: dict) -> dict:
    """Flatten the milestones dict into top-level columns with consistent names."""
    return {f"milestone_{k}": v for k, v in milestones.items()}


def write_funding_report(timelines: list, spreadsheet_id: str):
    """Write per-project current state to the Funding Report tab.

    timelines: list of dicts (output of ProjectTimeline.to_dict()) with extra
               'project_id' and 'customer_name' fields populated.
    """
    creds = _load_credentials()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)

    # ---- Funding Report tab (one row per project) ----
    headers = [
        "project_id", "customer_name", "fetched_at",
        "ntp_status", "install_status", "domestic_content_status", "credit_result",
        "ntp_submitted", "ntp_approved", "ntp_rejected",
        "credit_inquiry", "customer_account_created", "contract_signed",
        "payment_method_added", "install_pkg_submitted", "install_submitted",
        "open_stipulations_count", "cleared_stipulations_count",
        "doc_types_uploaded", "doc_types_approved",
        "event_count",
    ]
    ws = _get_or_create_worksheet(sh, "Funding Report", headers)

    # Clear existing data rows (keep header)
    ws.batch_clear(["A2:Z10000"])

    rows = []
    for t in timelines:
        cs = t.get("current_status", {})
        ms = t.get("milestones", {})
        stips = t.get("stipulations", [])
        docs = t.get("documents", {})

        open_stips = [s for s in stips if "cleared_at" not in s]
        cleared_stips = [s for s in stips if "cleared_at" in s]

        approved_doc_types = [
            dt for dt, items in docs.items()
            if any("approved_at" in i for i in items)
        ]

        rows.append([
            t.get("project_id", ""),
            t.get("customer_name", ""),
            datetime.utcnow().isoformat(),
            cs.get("ntp", ""),
            cs.get("install", ""),
            cs.get("domestic_content", ""),
            cs.get("credit_result", ""),
            ms.get("ntp_submitted", ""),
            ms.get("ntp_approved", ""),
            ms.get("ntp_rejected", ""),
            ms.get("credit_inquiry", ""),
            ms.get("customer_account_created", ""),
            ms.get("contract_signed", ""),
            ms.get("payment_method_added", ""),
            ms.get("install_pkg_submitted", ""),
            ms.get("install_submitted", ""),
            len(open_stips),
            len(cleared_stips),
            ", ".join(sorted(docs.keys())),
            ", ".join(sorted(approved_doc_types)),
            t.get("event_count", 0),
        ])

    if rows:
        ws.append_rows(rows, value_input_option="RAW")
    print(f"Funding Report: {len(rows)} projects written")

    # ---- Stipulations tab ----
    stip_headers = [
        "project_id", "customer_name", "stip_type",
        "flagged_at", "flagged_by", "cleared_at", "is_open",
    ]
    stip_ws = _get_or_create_worksheet(sh, "Stipulations", stip_headers)
    stip_ws.batch_clear(["A2:Z10000"])

    stip_rows = []
    for t in timelines:
        for s in t.get("stipulations", []):
            stip_rows.append([
                t.get("project_id", ""),
                t.get("customer_name", ""),
                s.get("type", ""),
                s.get("flagged_at", ""),
                s.get("flagged_by", ""),
                s.get("cleared_at", ""),
                "cleared_at" not in s,
            ])
    if stip_rows:
        stip_ws.append_rows(stip_rows, value_input_option="RAW")
    print(f"Stipulations: {len(stip_rows)} rows written")

    # ---- Audit Events tab (flat event log) ----
    event_headers = [
        "project_id", "customer_name", "timestamp",
        "actor", "actor_type", "event_type", "details", "raw_line",
    ]
    ev_ws = _get_or_create_worksheet(sh, "Audit Events", event_headers)
    ev_ws.batch_clear(["A2:Z100000"])

    event_rows = []
    for t in timelines:
        for e in t.get("events", []):
            event_rows.append([
                t.get("project_id", ""),
                t.get("customer_name", ""),
                e.get("timestamp", ""),
                e.get("actor", ""),
                e.get("actor_type", ""),
                e.get("event_type", ""),
                json.dumps(e.get("details", {})),
                e.get("raw_line", ""),
            ])
    # Sort newest first
    event_rows.sort(key=lambda r: r[2] or "", reverse=True)

    # Sheets has a 5M cell limit per sheet - chunk if huge
    CHUNK = 5000
    for i in range(0, len(event_rows), CHUNK):
        ev_ws.append_rows(event_rows[i:i+CHUNK], value_input_option="RAW")
    print(f"Audit Events: {len(event_rows)} rows written")
