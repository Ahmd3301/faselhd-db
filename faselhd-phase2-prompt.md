# FaselHD Phase 2 — Auto-Update System

You are working inside an existing Scrapy project at `faselhd-scraper/`.
Read `ANALYSIS.md` first to understand exactly what was built in Phase 1.
Do not modify any existing file unless explicitly instructed below.

---

## Context — What Exists

- 8 JSON files in `output/` — 24,019 items total
- Each file schema: `{ section, base_url, scraped_at, total, items[] }`
- Each item: `{ rank, slug, name, img, link, added_at }`
- `rank: 1` = most recently added item (page 1, position 1)
- Slugs are URL-decoded Arabic strings, e.g. `"مسلسل-stranger-things"`
- Links normalized to `www.fasel-hd.cam`
- Reusable: `normalize_link()`, `extract_slug()`, CSS selectors, all 3 pipelines

---

## Goal

Build an **auto-update system** that:
1. Detects new items added to the site since last scrape
2. Prepends them to the correct `output/*.json` files
3. Runs on GitHub Actions, triggered every 10 minutes via cron-job.org
4. Commits and pushes only when changes are found

---

## What to Build

### File: `update.py` — Main Update Orchestrator

Entry point: `python update.py [--section series] [--debug]`

**Algorithm per section:**

```
live_page1 = scrape page 1 from site (24 items)
db_page1   = output/{section}.json items[0..23]

Compare by slug:
  find i = index in live_page1 where live_page1[i].slug == db_page1[0].slug

Case i == 0:  no new items → skip section
Case 0 < i < 24: new_items = live_page1[0..i-1] → prepend to DB → done
Case i == -1:  entire page 1 is new → fetch page 2 and repeat comparison
               keep fetching pages until a match is found or 10 pages checked
               collect all new items → prepend to DB → done
```

**Prepend logic:**
- New items get ranks 1, 2, 3... (most recent first)
- Existing items get re-ranked starting from len(new_items)+1
- Update `total` count
- Update `scraped_at` to current UTC timestamp
- Set `added_at` on new items to current UTC timestamp

**Output:** print a summary per section:
```
[series]   +4 new items  → output/series.json updated
[movies]   no changes    → skipped
[anime]    +24 new items → output/anime.json updated (full page)
```

---

### File: `faselhd_scraper/spiders/update_spider.py` — Scrapy Spider for Update

A minimal Scrapy spider that scrapes only 1-10 pages per section (not full site).

- Accepts: `-a section=series -a max_pages=5`
- Uses identical CSS selectors as `faselhd_spider.py`
- Uses identical settings (headers, delays, retry)
- Returns items via existing pipelines (reuse ValidationPipeline, DuplicatesPipeline)
- Does NOT write to `output/` — returns items to `update.py` via Scrapy's `CrawlerProcess` API

---

### File: `github_push.py` — Git Operations

Called by `update.py` after writing updated JSON files.

```python
def push_if_changed(changed_sections: list[str]) -> bool:
    # git add output/{section}.json for each changed section
    # git commit -m "auto-update: {sections} +{count} items [{timestamp}]"
    # git push
    # return True if pushed, False if nothing to push
```

Use Python's `subprocess` to call git. Do not use any git library.
Read git credentials from environment variables:
- `GIT_USER_EMAIL`
- `GIT_USER_NAME`
These are set in GitHub Actions secrets — do not hardcode.

---

### File: `.github/workflows/update.yml` — GitHub Actions Workflow

```yaml
name: Auto Update FaselHD Database

on:
  workflow_dispatch:          # triggered by cron-job.org HTTP POST
  schedule:
    - cron: '*/30 * * * *'   # fallback: every 30min via GitHub native cron

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 8        # must finish before next 10-min trigger

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.PAT_TOKEN }}
          fetch-depth: 1

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - run: pip install -r requirements.txt

      - run: python update.py
        env:
          GIT_USER_EMAIL: ${{ secrets.GIT_USER_EMAIL }}
          GIT_USER_NAME:  ${{ secrets.GIT_USER_NAME }}

      - name: Show summary
        run: cat update_log.txt || echo "No log file"
```

**Important:** the workflow must configure git identity before pushing:
```bash
git config user.email "$GIT_USER_EMAIL"
git config user.name  "$GIT_USER_NAME"
```
Do this inside `github_push.py` before the commit step.

---

### File: `update_log.txt` — Run Log

`update.py` must write a plain text log after each run:
```
Run: 2026-06-03T10:00:00Z
[movies]      +2  → 13959 total
[series]      +0  → skipped
[anime]       +8  → 1868 total
[asian-series]+0  → skipped
[hindi]       +0  → skipped
[asian-movies]+0  → skipped
[anime-movies]+1  → 392 total
[tvshows]     +0  → skipped
Total new: 11 items across 3 sections
Duration: 47s
```

This file is committed alongside the JSON changes so every run is auditable in git history.

---

## cron-job.org Setup Instructions

Write a `SETUP.md` file with exact steps:

1. Create a GitHub Personal Access Token (PAT) with `repo` and `workflow` scopes
2. Add these GitHub Actions secrets to the repo:
   - `PAT_TOKEN` — the PAT
   - `GIT_USER_EMAIL` — any email
   - `GIT_USER_NAME` — any name
3. Go to cron-job.org → Create cronjob:
   - URL: `https://api.github.com/repos/{USERNAME}/{REPO}/actions/workflows/update.yml/dispatches`
   - Method: POST
   - Headers:
     ```
     Authorization: Bearer {PAT_TOKEN}
     Accept: application/vnd.github+json
     Content-Type: application/json
     ```
   - Body: `{"ref":"main"}`
   - Schedule: every 10 minutes
4. Test manually: trigger once from cron-job.org dashboard, check GitHub Actions tab

---

## Edge Cases to Handle

1. **Section has 0 items in DB** (empty file): run full scrape for that section only
2. **Site returns 0 items on page 1**: log warning, skip section, do not modify DB
3. **Slug not found in first 10 pages**: log warning, stop searching, prepend what was found
4. **Git push fails** (conflict, auth error): log error, exit with code 1 so GitHub Actions marks the run as failed
5. **Network timeout during update**: retry 3x with 10s delay, then skip section

---

## Constraints

- Do not modify: `faselhd_spider.py`, `pipelines.py`, `items.py`, `settings.py`
- Reuse existing: `normalize_link()`, `extract_slug()`, CSS selectors
- No new dependencies beyond what is already in `requirements.txt`
- All delays and anti-bot measures from Phase 1 apply here too
- The update for all 8 sections must complete within 7 minutes (GitHub Actions timeout is 8 min)

---

## Deliverables

When done, confirm:
- [ ] `update.py` working locally: `python update.py --section series`
- [ ] `update_spider.py` integrated with existing Scrapy project
- [ ] `github_push.py` tested with a dry-run flag: `python github_push.py --dry-run`
- [ ] `.github/workflows/update.yml` valid YAML
- [ ] `SETUP.md` written with exact cron-job.org instructions
- [ ] `update_log.txt` generated after test run
