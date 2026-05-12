"""
Writes parsed project data to Supabase via direct REST API calls.

Why not the supabase-py client? Supabase's new key format (sb_secret_...)
doesn't yet work correctly with the supabase-py 2.x client for write
operations. The REST API itself accepts the new keys fine, so we just
call PostgREST directly.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Iterable


def _flatten_events_for_insert(timeline_dict):
    project_id = timeline_dict.get("project_id")
    customer_name = timeline_dict.get("customer_name")
    rows = []
    for e in timeline_dict.get("events", []):
        if not e.get("timestamp"):
            continue
        rows.append({
            "project_id": project_id,
            "customer_name": customer_name,
            "event_timestamp": e["timestamp"],
            "actor": e.get("actor"),
            "actor_type": e.get("actor_type"),
            "event_type": e.get("event_type"),
            "details": e.get("details") or {},
            "raw_line": e.get("raw_line", ""),
        })
    return rows


def _build_snapshot_row(timeline_dict):
    cs = timeline_dict.get("current_status") or {}
    ms = timeline_dict.get("milestones") or {}
    stips = timeline_dict.get("stipulations") or []
    docs = timeline_dict.get("documents") or {}

    open_stips = [s for s in stips if "cleared_at" not in s]
    cleared_stips = [s for s in stips if "cleared_at" in s]

    oldest_open = None
    if open_stips:
        oldest_open = min(
            (s.get("flagged_at") for s in open_stips if s.get("flagged_at")),
            default=None,
        )

    approved_doc_types = sorted([
        dt for dt, items in docs.items()
        if any("approved_at" in i for i in items)
    ])
    uploaded_doc_types = sorted(docs.keys())

    return {
        "project_id": timeline_dict.get("project_id"),
        "customer_name": timeline_dict.get("customer_name"),
        "ntp_status": cs.get("ntp"),
        "install_status": cs.get("install"),
        "domestic_content_status": cs.get("domestic_content"),
        "credit_result": cs.get("credit_result"),
        "ntp_submitted_at": ms.get("ntp_submitted"),
        "ntp_approved_at": ms.get("ntp_approved"),
        "ntp_rejected_at": ms.get("ntp_rejected"),
        "contract_signed_at": ms.get("contract_signed"),
        "customer_account_created_at": ms.get("customer_account_created"),
        "payment_method_added_at": ms.get("payment_method_added"),
        "install_pkg_submitted_at": ms.get("install_pkg_submitted"),
        "install_submitted_at": ms.get("install_submitted"),
        "credit_inquiry_at": ms.get("credit_inquiry"),
        "open_stipulations_count": len(open_stips),
        "cleared_stipulations_count": len(cleared_stips),
        "oldest_open_stip_flagged_at": oldest_open,
        "doc_types_uploaded": uploaded_doc_types,
        "doc_types_approved": approved_doc_types,
        "total_event_count": timeline_dict.get("event_count", 0),
    }


def _chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _get_auth():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY env vars must both be set")
    return url, key


def _post(table, rows, on_conflict=None, ignore_duplicates=False):
    url, key = _get_auth()
    endpoint = f"{url}/rest/v1/{table}"
    if on_conflict:
        endpoint += f"?on_conflict={on_conflict}"

    prefer_parts = ["return=minimal"]
    if on_conflict:
        prefer_parts.append("resolution=" + ("ignore-duplicates" if ignore_duplicates else "merge-duplicates"))

    body = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": ",".join(prefer_parts),
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase write failed: HTTP {e.code} for {table}: {err_body}") from e


def write_to_supabase(timelines):
    all_event_rows = []
    for t in timelines:
        all_event_rows.extend(_flatten_events_for_insert(t))

    events_chunks_written = 0
    if all_event_rows:
        for chunk in _chunk(all_event_rows, 500):
            try:
                _post(
                    "lightreach_audit_events",
                    chunk,
                    on_conflict="project_id,event_timestamp,raw_line",
                    ignore_duplicates=True,
                )
                events_chunks_written += 1
            except Exception as e:
                print(f"  Event insert chunk failed: {e}", file=sys.stderr)
                raise

    snapshot_rows = [_build_snapshot_row(t) for t in timelines if t.get("project_id")]
    snapshot_count = 0
    if snapshot_rows:
        for chunk in _chunk(snapshot_rows, 500):
            _post(
                "lightreach_project_snapshot",
                chunk,
                on_conflict="project_id",
                ignore_duplicates=False,
            )
            snapshot_count += len(chunk)

    return {
        "events_processed": len(all_event_rows),
        "events_chunks_written": events_chunks_written,
        "snapshots_upserted": snapshot_count,
    }
