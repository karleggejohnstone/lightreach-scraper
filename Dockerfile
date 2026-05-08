# Playwright's official image ships Chromium and all required system deps,
# which saves a lot of headaches on Railway compared to a generic Python base.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Default cmd; Railway cron job will override or just run this
CMD ["python", "-m", "src.main"]
