"""
Playwright scraper for the LightReach/Palmetto portal.

Strategy:
1. Load saved session state from disk (cookies + localStorage).
2. If session is invalid, fall back to fresh login (requires creds in env).
3. Navigate to projects list, paginate, collect project IDs.
4. For each project, open the Activity tab, dump the full audit text.
5. Save raw audit text per project to a local file (audit log sheet) and
   pass to the parser for structured extraction.

NOTE on portal selectors: The CSS selectors below are PLACEHOLDERS based on
common React/SPA patterns. You'll need to inspect the live portal in
DevTools and update them. Search this file for "TODO_SELECTOR" to find
each one. Run `python -m src.scraper --headed --debug` first locally to
walk through interactively before deploying to Railway.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeout


PORTAL_BASE = "https://palmetto.finance"
LOGIN_URL = f"{PORTAL_BASE}/accounts/login"  # TODO_SELECTOR verify
PROJECTS_URL = f"{PORTAL_BASE}/accounts"      # TODO_SELECTOR verify

SESSION_PATH = Path(os.environ.get("SESSION_PATH", "./session.json"))
RAW_OUTPUT_DIR = Path(os.environ.get("RAW_OUTPUT_DIR", "./raw_audits"))


def manual_login_and_save(playwright) -> None:
    """Open a headed browser, let the user log in (incl. 2FA), then save storage_state.

    This is the one-shot first-run flow. Avoids the brittleness of auto-filling
    credentials and racing networkidle against a 2FA prompt.
    """
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    # Visit PROJECTS_URL, not LOGIN_URL — Palmetto uses Auth0, and hitting the
    # protected page triggers a redirect to Auth0 with a fresh state token.
    page.goto(PROJECTS_URL)
    print("Log in manually in the browser window (handle 2FA if prompted).")
    print(f"Once you land on the logged-in dashboard, come back here and press Enter.")
    try:
        input()
    except EOFError:
        pass
    context.storage_state(path=str(SESSION_PATH))
    print(f"Session saved to {SESSION_PATH}")
    browser.close()


def load_session_or_login(playwright, headless=True) -> tuple[BrowserContext, "Browser"]:
    """Return a browser context with a valid authenticated session."""
    browser = playwright.chromium.launch(headless=headless)

    if SESSION_PATH.exists():
        context = browser.new_context(storage_state=str(SESSION_PATH))
        page = context.new_page()
        page.goto(PROJECTS_URL)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        # Check if we got redirected to login - if so, session is dead
        if "login" in page.url.lower() or "auth" in page.url.lower():
            print("Saved session expired, falling back to fresh login...", file=sys.stderr)
            page.close()
            context.close()
        else:
            page.close()
            return context, browser

    # Fresh login
    context = browser.new_context()
    page = context.new_page()
    page.goto(LOGIN_URL)

    email = os.environ.get("LIGHTREACH_EMAIL")
    password = os.environ.get("LIGHTREACH_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "No saved session and LIGHTREACH_EMAIL / LIGHTREACH_PASSWORD env vars not set. "
            "For first-time setup, run `python -m src.scraper --headed --save-session` "
            "and log in manually; the session will be saved to session.json."
        )

    # TODO_SELECTOR: confirm these field selectors against the live login page
    page.fill('input[type="email"], input[name="email"]', email)
    page.fill('input[type="password"], input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded", timeout=30000)

    # Save session for next time
    context.storage_state(path=str(SESSION_PATH))
    page.close()
    return context, browser


def collect_project_ids(page: Page, projects_url: str = PROJECTS_URL) -> list[dict]:
    """Walk the projects list at projects_url, paginating, and return
    [{project_id, customer_name, url}, ...]."""
    page.goto(projects_url)
    page.wait_for_load_state("domcontentloaded", timeout=20000)

    projects = []
    seen_ids = set()
    max_pages = 100  # safety bound

    for page_num in range(max_pages):
        # Wait for project rows to render
        try:
            page.wait_for_selector('a[href*="/accounts/"]', timeout=10000)  # TODO_SELECTOR
        except PlaywrightTimeout:
            print(f"No project rows found on page {page_num}", file=sys.stderr)
            break

        # Extract project links from this page
        # TODO_SELECTOR: adjust the href pattern - could be /accounts/{id}, /projects/{id}, etc.
        rows = page.eval_on_selector_all(
            'a[href*="/accounts/"]',
            """elements => elements.map(el => {
                const href = el.getAttribute('href');
                // Extract ID from URL - adjust regex if needed
                const match = href.match(/accounts\\/([a-f0-9]{24})/);
                if (!match) return null;
                // Try to find a customer name nearby (sibling text, parent row, etc.)
                let nameEl = el.querySelector('.customer-name, [data-test="customer-name"]')
                            || el.closest('tr')?.querySelector('.customer-name, [data-test="customer-name"]')
                            || el;
                return {
                    project_id: match[1],
                    customer_name: nameEl?.innerText?.trim().split('\\n')[0] || null,
                    href: href,
                };
            }).filter(Boolean)"""
        )

        new_count = 0
        for row in rows:
            if row["project_id"] not in seen_ids:
                seen_ids.add(row["project_id"])
                row["url"] = f"{PORTAL_BASE}{row['href']}" if row["href"].startswith("/") else row["href"]
                projects.append(row)
                new_count += 1

        print(f"Page {page_num + 1}: found {new_count} new projects ({len(projects)} total)", file=sys.stderr)

        if new_count == 0:
            break

        # Pagination: a <button> whose label span text is exactly "Next".
        next_btn = page.query_selector('button:has(span:text-is("Next"))')
        if next_btn and not next_btn.is_disabled():
            next_btn.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        else:
            break  # no more pages

    return projects


def fetch_audit_log(page: Page, project: dict) -> Optional[str]:
    """Open a project's History page and return the raw audit text."""
    history_url = project["url"].rstrip("/") + "/history"
    page.goto(history_url)
    page.wait_for_load_state("domcontentloaded", timeout=20000)

    # Each entry's time cell has a full ISO timestamp in its title attribute.
    # Use that as a "ready" signal — at least one entry has rendered.
    try:
        page.wait_for_selector('div[title*="T"][title*="Z"]', timeout=10000)
    except PlaywrightTimeout:
        print(f"  No audit entries rendered for {project['project_id']}", file=sys.stderr)
        return None

    # Defensive scroll in case older entries lazy-load.
    for _ in range(20):
        prev = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        new = page.evaluate("document.body.scrollHeight")
        if new == prev:
            break

    return page.evaluate(
        "() => (document.querySelector('main') || document.body).innerText"
    )


def run(headless=True, save_session_only=False, project_urls=None):
    RAW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    project_urls = project_urls or [PROJECTS_URL]

    # On Railway, session.json is provided via a base64-encoded env var.
    # Decode it to disk on startup if present and the file isn't already there.
    session_b64 = os.environ.get("SESSION_BASE64")
    if session_b64 and not SESSION_PATH.exists():
        import base64
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_bytes(base64.b64decode(session_b64))
        print(f"Decoded SESSION_BASE64 -> {SESSION_PATH}", file=sys.stderr)

    with sync_playwright() as pw:
        if save_session_only:
            manual_login_and_save(pw)
            return

        context, browser = load_session_or_login(pw, headless=headless)

        page = context.new_page()
        projects = []
        seen_ids = set()
        for url in project_urls:
            print(f"Collecting from {url}", file=sys.stderr)
            for proj in collect_project_ids(page, url):
                if proj["project_id"] not in seen_ids:
                    seen_ids.add(proj["project_id"])
                    projects.append(proj)
        print(f"Collected {len(projects)} unique projects across {len(project_urls)} URL(s)", file=sys.stderr)

        results = []
        for i, proj in enumerate(projects):
            print(f"[{i+1}/{len(projects)}] {proj['project_id']} - {proj.get('customer_name')}", file=sys.stderr)
            try:
                text = fetch_audit_log(page, proj)
                if text:
                    out_path = RAW_OUTPUT_DIR / f"{proj['project_id']}.txt"
                    out_path.write_text(text)
                    proj["raw_path"] = str(out_path)
                    proj["fetched_at"] = datetime.utcnow().isoformat()
                results.append(proj)
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                proj["error"] = str(e)
                results.append(proj)

        # Save the index of projects fetched
        index_path = RAW_OUTPUT_DIR / "_index.json"
        index_path.write_text(json.dumps(results, indent=2))
        print(f"Index written to {index_path}")

        browser.close()
        return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true", help="Run with visible browser (for debugging/first login)")
    p.add_argument("--save-session", action="store_true", help="Just log in and save session, then exit")
    p.add_argument(
        "--projects-url",
        action="append",
        help="Project list URL to scrape (repeatable). Defaults to /accounts. "
             "Example: --projects-url 'https://palmetto.finance/accounts?currentMilestone=install'",
    )
    args = p.parse_args()
    run(
        headless=not args.headed,
        save_session_only=args.save_session,
        project_urls=args.projects_url,
    )
