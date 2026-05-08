"""
Quick test: fire a sample alert to verify the Slack webhook works.

Usage:
    export SLACK_ALERT_WEBHOOK_URL='https://hooks.slack.com/services/...'
    export SLACK_ALERT_USER_ID='U0AB1LSPZRQ'
    python -m tests.test_alerts
"""

from src.alerts import send_alert


def main():
    print("Sending test alert to Slack...")
    ok = send_alert(
        "generic",
        "Test alert from the LightReach scraper - if you see this in Slack, alerting works.",
        details={
            "test_field_1": "value 1",
            "test_field_2": [1, 2, 3],
            "note": "This is a synthetic test, no actual scrape failure occurred.",
        },
    )
    if ok:
        print("Alert sent successfully. Check your Slack DMs.")
    else:
        print("Alert failed to send. Check stderr above for the reason.")


if __name__ == "__main__":
    main()
