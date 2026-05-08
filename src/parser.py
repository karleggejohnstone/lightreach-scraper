"""
Parses LightReach project audit logs into structured events.

Audit log format observations:
- Date headers: "Apr 27, 2026" (date alone on a line)
- Time headers: "3:19 PM" (time alone on a line, applies to subsequent event)
- Actor + action on the line after the time
- Optional follow-up lines for details (email subjects, document names, field diffs)
- Date headers are listed newest-first in the portal

Strategy: walk lines top-to-bottom. Maintain "current date" state.
When we see a time, the next line(s) form an event tied to (date, time).
Classify the event by matching the actor+action against known patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# ---------- Regex patterns ----------

DATE_HEADER_RE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})$")
TIME_HEADER_RE = re.compile(r"^(\d{1,2}):(\d{2})\s+(AM|PM)$")

# Actor types - order matters, more specific first
ACTOR_PATTERNS = [
    ("lightreach_support", re.compile(r"^LightReach Support\s+")),
    ("customer", re.compile(r"^Customer\s+")),
    ("contract_signed", re.compile(r"^Contract Signed by\s+(?P<email>\S+)")),
    ("sitecapture", re.compile(r"^integrations@sitecapture\.com\s+")),
    ("subhub_api", re.compile(r"^lightreach\.api\+\w+@subcontractorhub\.com\s+")),
    ("internal_user", re.compile(r"^([\w.]+@livsmartsolar\.com)\s+")),
    # Named user: "FirstName LastName" followed by a known action verb.
    # Without the verb constraint, email subjects like "Administrative Stipulation Cleared for Huy Ma"
    # falsely match because "Administrative Stipulation" looks like a name.
    # Verbs are restricted to action verbs (not noun phrases like "Notice to") to avoid
    # matching email subjects like "Huy Ma Notice to Proceed PACKAGE APPROVED".
    ("named_user", re.compile(
        r"^([A-Z][a-z]+ [A-Z][a-z]+)\s+"
        r"(updated|uploaded|Submitted|Saved|created|voided|sent|approved|submitted|inquired|flagged|cleared|processed|invited|Install status|payment method)"
    )),
]

# Event type patterns - matched against the rest of the line after the actor
EVENT_PATTERNS = [
    # Status changes
    ("ntp_status_change", re.compile(r"Notice to Proceed status changed to (?P<status>\w+)")),
    ("install_status_change", re.compile(r"Install status changed to (?P<status>\w+)")),
    ("dc_eligibility_change", re.compile(r"updated Domestic Content Eligibility Status to (?P<status>.+)$")),

    # Stipulations
    ("stip_flagged", re.compile(r"flagged (?P<stip_type>\w+) stipulation")),
    ("stip_cleared", re.compile(r"cleared (?P<stip_type>\w+) stipulation")),

    # Documents
    ("doc_uploaded", re.compile(r"uploaded (?P<filenames>.+?) as (?P<doc_type>.+?) documents?$")),
    ("doc_approved", re.compile(r"approved (?P<doc_type>.+?) document\s+(?P<filenames>.+)$")),
    ("doc_submitted", re.compile(r"submitted a (?P<doc_type>.+?) document for review")),

    # Contracts/Quotes
    ("contract_sent", re.compile(r"sent a contract to (?P<email>\S+)")),
    ("contract_signed", re.compile(r"^Contract Signed by\s+(?P<email>\S+)")),
    ("contract_created", re.compile(r"created a contract")),
    ("contract_voided", re.compile(r"voided a contract")),
    ("quote_created", re.compile(r"created a quote")),
    ("quote_voided", re.compile(r"voided a quote")),

    # Account / customer
    ("account_created", re.compile(r"created account (?P<name>.+)$")),
    ("customer_account_created", re.compile(r"created their Palmetto account")),
    ("customer_invited", re.compile(r"invited the customer to create their Palmetto account")),
    ("credit_inquiry", re.compile(r"inquired credit history for (?P<name>.+?):\s*(?P<result>\w+)")),
    ("payment_method_added", re.compile(r"payment method added")),
    ("utility_bill_extracted", re.compile(r"processed a utility bill extraction")),

    # Install package events
    ("install_pkg_submitted", re.compile(r"Submitted Install Package")),
    ("install_pkg_saved", re.compile(r"Saved Install Package")),

    # Email notifications
    ("email_sent", re.compile(r"sent an email to (?P<recipient>\S+)")),

    # Adder / generic updates (catch-all, matched LAST)
    ("adder_updated", re.compile(r"^updated$")),  # standalone "updated" usually precedes "Adder" line
    ("field_updated", re.compile(r"updated (?P<field>.+)$")),
]


# ---------- Data classes ----------

@dataclass
class Event:
    timestamp: Optional[str]  # ISO format, may be None if date/time partial
    actor: str
    actor_type: str
    event_type: str
    raw_line: str
    details: dict = field(default_factory=dict)
    extra_lines: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class ProjectTimeline:
    customer_name: Optional[str] = None
    project_id: Optional[str] = None
    events: list[Event] = field(default_factory=list)

    # Derived fields populated by .compute_summary()
    current_status: dict = field(default_factory=dict)
    milestones: dict = field(default_factory=dict)
    stipulations: list[dict] = field(default_factory=list)
    documents: dict = field(default_factory=dict)

    def compute_summary(self):
        """Walk events oldest-to-newest to derive current state."""
        ordered = sorted(
            [e for e in self.events if e.timestamp],
            key=lambda e: e.timestamp,
        )

        ntp_status = None
        install_status = None
        dc_status = None
        milestones = {}
        active_stips = []
        cleared_stips = []
        documents_by_type = {}

        for e in ordered:
            ts = e.timestamp

            if e.event_type == "ntp_status_change":
                ntp_status = e.details.get("status")
                key = f"ntp_{ntp_status}"
                if key not in milestones:
                    milestones[key] = ts

            elif e.event_type == "install_status_change":
                install_status = e.details.get("status")
                milestones[f"install_{install_status}"] = ts

            elif e.event_type == "dc_eligibility_change":
                dc_status = e.details.get("status")

            elif e.event_type == "stip_flagged":
                active_stips.append({
                    "type": e.details.get("stip_type"),
                    "flagged_at": ts,
                    "flagged_by": e.actor,
                })

            elif e.event_type == "stip_cleared":
                # match to most recent active stip of same type
                stip_type = e.details.get("stip_type")
                for s in reversed(active_stips):
                    if s["type"] == stip_type and "cleared_at" not in s:
                        s["cleared_at"] = ts
                        cleared_stips.append(s)
                        break
                else:
                    cleared_stips.append({
                        "type": stip_type,
                        "cleared_at": ts,
                        "cleared_by": e.actor,
                    })

            elif e.event_type == "contract_signed":
                if "contract_signed" not in milestones:
                    milestones["contract_signed"] = ts

            elif e.event_type == "customer_account_created":
                milestones["customer_account_created"] = ts

            elif e.event_type == "credit_inquiry":
                milestones["credit_inquiry"] = ts
                self.current_status["credit_result"] = e.details.get("result")

            elif e.event_type == "payment_method_added":
                milestones["payment_method_added"] = ts

            elif e.event_type == "install_pkg_submitted":
                milestones["install_pkg_submitted"] = ts

            elif e.event_type == "doc_uploaded":
                doc_type = e.details.get("doc_type")
                if doc_type:
                    documents_by_type.setdefault(doc_type, []).append({
                        "uploaded_at": ts,
                        "uploaded_by": e.actor,
                        "filenames": e.details.get("filenames"),
                    })

            elif e.event_type == "doc_approved":
                doc_type = e.details.get("doc_type")
                if doc_type and doc_type in documents_by_type:
                    documents_by_type[doc_type][-1]["approved_at"] = ts

        self.current_status.update({
            "ntp": ntp_status,
            "install": install_status,
            "domestic_content": dc_status,
        })
        # Open stipulations = active without cleared_at
        self.stipulations = [s for s in active_stips if "cleared_at" not in s] + cleared_stips
        self.milestones = milestones
        self.documents = documents_by_type

    def to_dict(self):
        return {
            "customer_name": self.customer_name,
            "project_id": self.project_id,
            "current_status": self.current_status,
            "milestones": self.milestones,
            "stipulations": self.stipulations,
            "documents": self.documents,
            "event_count": len(self.events),
            "events": [e.to_dict() for e in self.events],
        }


# ---------- Parser ----------

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _build_timestamp(date_parts, time_parts):
    """Return ISO timestamp from parsed date + time, or None."""
    if not date_parts or not time_parts:
        return None
    month, day, year = date_parts
    hour, minute, ampm = time_parts
    hour = int(hour)
    minute = int(minute)
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    try:
        return datetime(year, MONTH_MAP[month], day, hour, minute).isoformat()
    except (KeyError, ValueError):
        return None


def _classify_actor(line):
    """Return (actor_string, actor_type, remainder) or (None, None, None)."""
    for actor_type, pattern in ACTOR_PATTERNS:
        m = pattern.match(line)
        if m:
            # If the pattern captured a group 1 (actor name), use that.
            # Otherwise, use the full match (e.g. "LightReach Support ").
            if m.groups():
                actor_str = m.group(1).strip()
                # Find where the actor's name ends in the original line
                actor_end = line.find(actor_str) + len(actor_str)
                remainder = line[actor_end:].strip()
            else:
                actor_str = line[:m.end()].strip()
                remainder = line[m.end():].strip()
            return actor_str, actor_type, remainder
    return None, None, line


def _classify_event(remainder, full_line):
    """Match the action portion against event patterns. Returns (event_type, details)."""
    # Special case: "Contract Signed by" is its own line, no actor prefix
    m = re.match(r"^Contract Signed by\s+(?P<email>\S+)", full_line)
    if m:
        return "contract_signed", m.groupdict()

    for event_type, pattern in EVENT_PATTERNS:
        m = pattern.search(remainder)
        if m:
            return event_type, m.groupdict()

    return "unknown", {}


def parse_audit_log(text: str) -> ProjectTimeline:
    """Parse a raw audit log string into a ProjectTimeline."""
    lines = [ln.strip() for ln in text.split("\n")]
    # Filter out blank lines but keep order
    lines = [ln for ln in lines if ln]

    timeline = ProjectTimeline()
    current_date = None
    current_time = None
    pending_event = None  # event waiting for follow-up lines

    i = 0
    while i < len(lines):
        line = lines[i]

        # Date header?
        m = DATE_HEADER_RE.match(line)
        if m:
            month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
            current_date = (month, day, year)
            current_time = None
            # Flush any pending event before changing date
            if pending_event:
                timeline.events.append(pending_event)
                pending_event = None
            i += 1
            continue

        # Time header?
        m = TIME_HEADER_RE.match(line)
        if m:
            current_time = (m.group(1), m.group(2), m.group(3))
            # Flush previous event
            if pending_event:
                timeline.events.append(pending_event)
                pending_event = None
            i += 1
            continue

        # Otherwise it's an event line OR a continuation of one
        actor, actor_type, remainder = _classify_actor(line)

        if actor:
            # New event
            if pending_event:
                timeline.events.append(pending_event)
            event_type, details = _classify_event(remainder, line)
            pending_event = Event(
                timestamp=_build_timestamp(current_date, current_time),
                actor=actor,
                actor_type=actor_type,
                event_type=event_type,
                raw_line=line,
                details=details,
            )
            # Try to capture customer name from credit inquiry or account creation
            if event_type == "credit_inquiry" and "name" in details:
                timeline.customer_name = details["name"]
            elif event_type == "account_created" and "name" in details:
                if not timeline.customer_name:
                    timeline.customer_name = details["name"]
        elif line.startswith("Contract Signed by"):
            # Edge case: contract signed line has no actor prefix
            if pending_event:
                timeline.events.append(pending_event)
            event_type, details = _classify_event(line, line)
            pending_event = Event(
                timestamp=_build_timestamp(current_date, current_time),
                actor=details.get("email", "customer"),
                actor_type="contract_signed",
                event_type="contract_signed",
                raw_line=line,
                details=details,
            )
        else:
            # Continuation line - attach to pending event as extra detail
            if pending_event:
                pending_event.extra_lines.append(line)

        i += 1

    # Flush final event
    if pending_event:
        timeline.events.append(pending_event)

    timeline.compute_summary()
    return timeline
