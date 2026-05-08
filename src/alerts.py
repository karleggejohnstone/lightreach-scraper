"""
Slack alerting for the daily scraper. Posts to a single channel via webhook.

Failure modes covered:
- Session expired (silent failure mode - scraper redirects to login but reports success)
- Zero projects collected (almost always a selector breakage)
- Sheet write failed (Sheets API auth/quota issues)
- Any unhandled exception (catch-all)

Webhook URL is read from SLACK_ALERT_WEBHOOK_URL env var.
User ID for @mention is read from SLACK_ALERT_USER_ID env var (optional but recommended).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone


ALERT_TYPES = {
    "session_expired": (":lock: Session Expired", "high"),
    "zero_projects": (":warning: Zero Projects Collected", "high"),
    "sheet_write_failed": (":x: Sheet Write Failed", "high"),
    "scrape_error": (":boom: Scrape Run Failed", "high"),
    "parser_warning": (":mag: Parser Warning", "low"),
    "generic": (":rotating_light: Scraper Alert", "medium"),
}


class AlertError(Exception):
    """Raised after sending an alert to halt the run with a clear signal."""
    pass


def _build_payload(alert_type, message, details=None):
    title, _ = ALERT_TYPES.get(alert_type, ALERT_TYPES["generic"])
    user_id = os.environ.get("SLACK_ALERT_USER_ID", "").strip()
    mention = f"<@{user_id}> " if user_id else ""

    railway_service = os.environ.get("RAILWAY_SERVICE_NAME", "lightreach-scraper")
    railway_url = os.environ.get("RAILWAY_PROJECT_URL", "")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{mention}*{message}*"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"*Service:* `{railway_service}`  •  *Time:* {timestamp}"},
        ]},
    ]

    if details:
        detail_text = json.dumps(details, indent=2, default=str)
        if len(detail_text) > 2500:
            detail_text = detail_text[:2500] + "\n... (truncated)"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```{detail_text}```"},
        })

    if railway_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Open Railway logs"},
                "url": railway_url,
            }],
        })

    return {"text": f"{title}: {message}", "blocks": blocks}


def send_alert(alert_type, message, details=None):
    """Post a Slack alert. Returns True on success, False on failure."""
    webhook_url = os.environ.get("SLACK_ALERT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print(f"[ALERT NOT SENT - no webhook configured] {alert_type}: {message}", file=sys.stderr)
        return False

    payload = _build_payload(alert_type, message, details)
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            if resp.status == 200 and body == "ok":
                return True
            print(f"[ALERT FAILED] HTTP {resp.status}: {body}", file=sys.stderr)
            return False
    except (urllib.error.URLError, socket.timeout) as e:
        print(f"[ALERT FAILED] {type(e).__name__}: {e}", file=sys.stderr)
        return False


def alert_on_exception(alert_type="scrape_error", message=None):
    """Send an alert based on the current exception (use inside an except block)."""
    exc_type, exc_value, _ = sys.exc_info()
    if exc_type is None:
        return False

    tb = traceback.format_exc()
    tb_lines = tb.splitlines()
    if len(tb_lines) > 30:
        tb = "\n".join(["... (earlier frames omitted)"] + tb_lines[-30:])

    return send_alert(
        alert_type,
        message or f"{exc_type.__name__}: {exc_value}",
        details={"traceback": tb},
    )
