"""
Smoke test for the audit log parser. Runs against a real fixture and checks
that the key derived fields come out correctly. Run with: python -m tests.test_parser

Not using pytest to keep the dependency surface small. Plain assertions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser import parse_audit_log


FIXTURE = Path(__file__).parent / "fixtures" / "sample_huy_ma.txt"


def test_huy_ma():
    text = FIXTURE.read_text()
    timeline = parse_audit_log(text)
    d = timeline.to_dict()

    # Customer name extracted from credit inquiry line
    assert d["customer_name"] == "Huy Ma", f"Got {d['customer_name']!r}"

    # Status reflects most recent state
    cs = d["current_status"]
    assert cs["ntp"] == "approved"
    assert cs["install"] == "submitted"
    assert cs["domestic_content"] == "Under Consideration"
    assert cs["credit_result"] == "Approved"

    # Key milestones present with correct timestamps
    ms = d["milestones"]
    assert ms["ntp_submitted"] == "2026-04-01T08:36:00"
    assert ms["ntp_approved"] == "2026-04-01T12:31:00"
    assert ms["contract_signed"] == "2026-04-01T11:23:00"
    assert ms["install_pkg_submitted"] == "2026-04-24T15:19:00"

    # Stipulation lifecycle: flagged 4/2, cleared 4/15
    stips = d["stipulations"]
    assert len(stips) == 1
    assert stips[0]["type"] == "administrative"
    assert stips[0]["flagged_at"] == "2026-04-02T09:04:00"
    assert stips[0]["cleared_at"] == "2026-04-15T15:01:00"

    # Documents tracked by type
    docs = d["documents"]
    assert "Identity Verification" in docs
    assert "Title Verification" in docs
    assert "Permit" in docs
    assert "Plan Set" in docs
    # Identity verification was approved
    iv = docs["Identity Verification"][0]
    assert "approved_at" in iv
    assert iv["approved_at"] == "2026-04-01T12:04:00"

    # No unknown events - parser handled everything
    unknowns = [e for e in d["events"] if e["event_type"] == "unknown"]
    assert len(unknowns) == 0, f"Unknown events: {[e['raw_line'] for e in unknowns]}"

    print(f"[PASS] {len(d['events'])} events parsed, 0 unknowns")
    print(f"       Status: NTP={cs['ntp']}, Install={cs['install']}")
    print(f"       Stips: 1 (cleared)")
    print(f"       Doc types: {len(docs)}")


if __name__ == "__main__":
    test_huy_ma()
    print("All tests passed.")
