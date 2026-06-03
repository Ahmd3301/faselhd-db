# Connecting cron-job.org to GitHub Actions

## What this does

cron-job.org sends an HTTP POST to GitHub every 10 minutes.
GitHub receives it and triggers the update workflow.
The workflow runs `update.py`, commits new items, and pushes to `main`.

## Prerequisites

1. A GitHub account
2. The repo pushed to GitHub (`faselhd-db`)

## Steps

### 1. Create a GitHub Personal Access Token (PAT)

1. Go to: https://github.com/settings/tokens/new
2. Name: `faselhd-auto-update`
3. Expiration: 90 days (recommended)
4. Scopes: `repo` (full) and `workflow`
5. Click "Generate token"
6. **Copy the token immediately** — you won't see it again

### 2. Add GitHub Actions Secrets

In your repo (`https://github.com/{USER}/faselhd-db/settings/secrets/actions`):

| Secret | Value |
|--------|-------|
| `PAT_TOKEN` | The PAT from step 1 |
| `GIT_USER_EMAIL` | Any email (e.g., `bot@example.com`) |
| `GIT_USER_NAME` | Any name (e.g., `FaselHD Bot`) |

### 3. Get Your Workflow Dispatch URL

```
https://api.github.com/repos/{YOUR_GITHUB_USERNAME}/faselhd-db/actions/workflows/update.yml/dispatches
```

Replace `{YOUR_GITHUB_USERNAME}` with your actual GitHub username.

### 4. Create a cron-job.org Account

1. Go to: https://cron-job.org
2. Sign up (free)
3. Verify your email

### 5. Create a New Cronjob

- **Title:** `FaselHD Auto Update`
- **URL:** Your dispatch URL from step 3
- **Method:** `POST`
- **Headers (add each):**
  - `Authorization: Bearer {YOUR_PAT_TOKEN}`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- **Request body:** `{"ref":"main"}`
- **Schedule:** Every 10 minutes (`Minutes` → `*/10`)
- **Enable:** ON

### 6. Test It Manually

1. Click "Run now" in cron-job.org dashboard
2. Go to https://github.com/{USER}/faselhd-db/actions
3. A workflow run should appear within 30 seconds
4. Click it → check logs → should say "no changes" or show new items

### 7. Verify Automatic Runs

After 10 minutes, check the Actions tab again. A second run should appear automatically.

## Monitoring

- **GitHub Actions tab** shows every run with full logs
- **update_log.txt** in the repo shows a history of every update
- **Commit messages** show: `auto-update: +N items [timestamp]`

## Costs

| Service | Cost | Notes |
|---------|------|-------|
| cron-job.org | Free | Up to 5 cronjobs, 1-min resolution |
| GitHub Actions | Free | 2,000 min/month on free tier |

At 10-min intervals: ~4,300 runs/month × ~1 min = ~4,300 min (exceeds free tier).
Use 30-minute intervals instead (already set in `update.yml` as fallback).
