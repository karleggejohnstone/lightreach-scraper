# LightReach Funding Report Scraper

Daily scraper that pulls the full audit log from every project in your LightReach/Palmetto portal, parses it into structured events, and writes it to Google Sheets.

## What it produces

Three Google Sheets tabs, refreshed daily:

1. **Funding Report** — one row per project with current status, milestone timestamps (NTP submitted/approved, contract signed, install scheduled, etc.), open vs. cleared stipulation counts, and document approval state.
2. **Stipulations** — one row per stipulation with flagged-at, cleared-at, and who handled it. Lets you sort by oldest open stip.
3. **Audit Events** — flat event log (newest first), every action ever logged on every project. Filterable by event type, actor, project, or date.

## Architecture

```
[ Railway cron, 7AM PT ]
        ↓
[ Playwright + saved session ]
        ↓
[ Walk projects list, scrape Activity tab text ]
        ↓
[ src/parser.py — turn audit text into structured events ]
        ↓
[ Google Sheets via service account ]
```

## First-time setup

### 1. Local install

```bash
git clone <your-repo>
cd lightreach-scraper
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Save a logged-in session locally

The portal will likely 2FA-challenge a Railway server IP, so we log in once on your laptop and ship the saved cookies up.

```bash
cp .env.example .env
# Edit .env: fill in LIGHTREACH_EMAIL, LIGHTREACH_PASSWORD
python -m src.scraper --headed --save-session
```

A browser will open, log in (handle 2FA if prompted), then close. `session.json` is written to your project root.

### 3. Set up Google Sheets

1. Create a new Google Sheet. Copy the ID from the URL (the long string between `/d/` and `/edit`).
2. Go to [Google Cloud Console](https://console.cloud.google.com), create a project, enable the **Google Sheets API**.
3. Create a **service account**, download the JSON key.
4. Open your sheet, click **Share**, paste the service account email (it's in the JSON as `client_email`), give it **Editor** access.
5. Add the sheet ID and the JSON content to `.env`:
   ```
   FUNDING_REPORT_SHEET_ID=1abc...xyz
   GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
   ```

### 4. Tune the selectors

The scraper has placeholder CSS selectors marked `TODO_SELECTOR` in `src/scraper.py`. They're educated guesses based on common React app patterns; the actual portal almost certainly uses different ones.

To tune them:

```bash
python -m src.scraper --headed
```

This runs with a visible browser. When it pauses or fails, open DevTools, find the right selectors, update `src/scraper.py`, and run again.

The three areas needing verification:
- `LOGIN_URL` and `PROJECTS_URL` — confirm these paths
- `collect_project_ids()` — selectors for the project list rows, customer name, and pagination
- `fetch_audit_log()` — selector for the Activity tab and the audit log container

### 5. Test the full pipeline locally

```bash
python -m src.main
```

If the Funding Report sheet populates, you're ready to deploy.

## Deploying to Railway

1. Push the repo to GitHub.
2. In Railway, **New Project → Deploy from GitHub** → pick this repo.
3. Add environment variables in Railway settings:
   - `LIGHTREACH_EMAIL`
   - `LIGHTREACH_PASSWORD`
   - `FUNDING_REPORT_SHEET_ID`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — paste the **full JSON** as the value
4. Upload `session.json` as a Railway file/volume. Easiest: add it as a secret base64-encoded, then a small startup script writes it to disk. Or use a [Railway Volume](https://docs.railway.com/reference/volumes) mounted at the path in `SESSION_PATH`.
5. Add a **Cron schedule** in Railway service settings: `0 15 * * *` runs daily at 7 AM Pacific.

### Session refresh

The saved session will eventually expire (typically every 30–90 days depending on the portal). When that happens the daily run will fail with a login redirect. To refresh:

```bash
# Locally
rm session.json
python -m src.scraper --headed --save-session
# Re-upload session.json to Railway
```

Consider adding a Slack alert later for "scrape failed" so you notice this.

## Project layout

```
src/
├── parser.py         # Audit log → structured events. Tested against real data.
├── scraper.py        # Playwright login + project walk + audit fetch
├── sheets.py         # Google Sheets writer (3 tabs)
├── main.py           # Orchestrates scrape → parse → write
└── __init__.py
tests/
└── fixtures/
    └── sample_huy_ma.txt   # Real audit log for parser testing
Dockerfile            # Playwright official image base
railway.json          # Build/deploy config
requirements.txt
.env.example
```

## Extending

A few additions that fit cleanly into this structure:

- **Slack notifications** — `src/slack_notify.py` that posts a daily diff (new stips, cleared stips, status changes) to `#ca-permitting`. Compare today's events against yesterday's snapshot saved in a fourth sheet tab.
- **Per-project timeline view** — generate one Google Doc per project with a chronological narrative of events, useful for handoffs and disputes.
- **Stuck-project alerts** — flag projects with NTP submitted >3 days ago, or open stips >5 days, in a "Needs Attention" tab.

## Compliance note

Verify scraping of your own authorized account is permitted under your LightReach/Palmetto agreement. The cleanest path if available is asking your rep for an API or richer export. If they don't offer one, scraping data you have legitimate access to is generally fine.
