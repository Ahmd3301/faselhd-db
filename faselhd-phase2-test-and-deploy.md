# FaselHD Phase 2 — Test Suite + GitHub Deploy

You are inside the existing `faselhd-scraper/` project.
Phase 2 code (update.py, update_spider.py, github_push.py, update.yml) is already written.
Now do three things in order: build a mock server, run all tests, then deploy to GitHub.

---

## STEP 1 — Mock Server

### File: `tests/mock_server.py`

A lightweight HTTP server using Python's built-in `http.server` (zero new dependencies).

It must serve fake HTML pages that match the exact structure of fasel-hd.cam:

```python
# Serves GET /{section}/page/{n}
# Returns HTML with exactly 24 .postDiv items (or fewer on last page)
# Structure must match the real site exactly:
#   #postList > .postDiv > a[href] > img[alt][data-src]
```

**Mock data rules:**
- Sections: all 8 (movies, series, anime, asian-series, asian-movies, hindi, anime-movies, tvshows)
- Pages per section: 3 pages × 24 items = 72 items per section
- Item format:
  ```
  name:  "مسلسل Mock-{section}-{rank}"
  slug:  "مسلسل-mock-{section}-{rank}"
  img:   "https://static.faselhdcdn.com/mock/{section}/{rank}.jpg"
  link:  "https://www.fasel-hd.cam/seasons/مسلسل-mock-{section}-{rank}"
  ```
- Page 4 and beyond: return 404

**State mutation for update testing:**
The mock server must support a special endpoint:
```
POST /admin/inject?section=series&count=5
```
This prepends N new fake items to section's page 1, pushing existing items down.
New items format: `"مسلسل Mock-{section}-NEW-{timestamp}-{i}"`

This simulates the site adding new content between scrape runs.

Run on: `http://localhost:8765`

---

## STEP 2 — Test Suite

### File: `tests/test_update.py`

Run with: `python tests/test_update.py`
No pytest needed — use Python's built-in `unittest`.

**Test sequence (run in this exact order):**

```
[TEST 1] Mock server is reachable
  GET http://localhost:8765/series/page/1
  Assert: status 200, contains 24 items in HTML
  Assert: item structure matches CSS selectors used by spider

[TEST 2] Mock server 404 on last page
  GET http://localhost:8765/series/page/4
  Assert: status 404

[TEST 3] Full scrape on mock server
  Temporarily point SCRAPER_BASE_URL env var to http://localhost:8765
  Run: scrapy crawl faselhd -a section=series (against mock)
  Assert: output/series.json created with 72 items
  Assert: rank 1 = first item, rank 72 = last item
  Assert: all slugs unique

[TEST 4] Update detects injected items
  Call POST /admin/inject?section=series&count=5
  Run: python update.py --section series --base-url http://localhost:8765
  Assert: output/series.json now has 77 items
  Assert: new items are at ranks 1-5
  Assert: old rank 1 item is now at rank 6
  Assert: scraped_at timestamp updated
  Assert: total field = 77

[TEST 5] Update detects full page of new items
  Call POST /admin/inject?section=series&count=24
  Run: python update.py --section series --base-url http://localhost:8765
  Assert: 24 new items prepended
  Assert: algorithm fetched page 2 to find the match point

[TEST 6] Update skips section with no changes
  Run: python update.py --section series --base-url http://localhost:8765
  (no inject called this time)
  Assert: output/series.json unchanged (same total, same scraped_at)
  Assert: log shows "skipped"

[TEST 7] Dry-run against real site
  Run: python update.py --section series --dry-run
  (uses real fasel-hd.cam, no --base-url override)
  Assert: exits without modifying output/series.json
  Assert: prints comparison result (X new items found or "no changes")
  Assert: no git operations performed

[TEST 8] github_push.py dry-run
  Run: python github_push.py --dry-run --sections series
  Assert: prints the git commands it would run
  Assert: does NOT actually commit or push
  Assert: exits with code 0
```

**Test runner output format:**
```
========================================
 FaselHD Phase 2 — Test Suite
========================================
[TEST 1] Mock server reachable ........... PASS
[TEST 2] Mock server 404 on last page .... PASS
[TEST 3] Full scrape on mock ............. PASS (72 items)
[TEST 4] Update detects 5 new items ...... PASS
[TEST 5] Update detects full page (24) ... PASS
[TEST 6] Update skips unchanged section .. PASS
[TEST 7] Dry-run vs real site ............ PASS (site reachable, X items found)
[TEST 8] github_push dry-run ............. PASS
========================================
All 8 tests passed. Ready to deploy.
========================================
```

If any test fails: print FAIL with reason, stop, do not proceed to deploy.

---

## STEP 3 — Deploy to GitHub

### File: `deploy.py`

Run with: `python deploy.py`

Only runs if all 8 tests passed (reads test result from `tests/.last_result`).

**Steps (in order):**

```python
# 1. Check tests passed
if not tests_passed():
    print("ERROR: Run tests first: python tests/test_update.py")
    exit(1)

# 2. Create GitHub repo using gh CLI
subprocess.run([
    "gh", "repo", "create", "faselhd-db",
    "--public",
    "--description", "Auto-updated database of FaselHD content (24,019+ items)",
    "--confirm"
])

# 3. Initialize git in current directory if not already
subprocess.run(["git", "init"])
subprocess.run(["git", "branch", "-M", "main"])

# 4. Add remote
subprocess.run([
    "git", "remote", "add", "origin",
    "https://github.com/{GH_USERNAME}/faselhd-db.git"
])
# Read GH_USERNAME from: gh api user --jq .login

# 5. Create .gitignore
write_gitignore()  # see contents below

# 6. Initial commit
subprocess.run(["git", "add", "."])
subprocess.run([
    "git", "commit", "-m",
    f"initial: {total_items} items across 8 sections | {timestamp}"
])

# 7. Push
subprocess.run(["git", "push", "-u", "origin", "main"])

# 8. Add GitHub Actions secrets using gh CLI
subprocess.run(["gh", "secret", "set", "PAT_TOKEN", "--body", pat_token])
subprocess.run(["gh", "secret", "set", "GIT_USER_EMAIL", "--body", git_email])
subprocess.run(["gh", "secret", "set", "GIT_USER_NAME", "--body", git_name])

# 9. Print final confirmation
print_deployment_summary()
```

**Ask for secrets interactively (getpass, never echo to terminal):**
```
GitHub Personal Access Token (PAT) with repo+workflow scopes: ****
Git commit email: ****
Git commit name: ****
```

**PAT requirements to tell the user:**
- Go to: https://github.com/settings/tokens/new
- Scopes needed: `repo` (full), `workflow`
- Expiration: 90 days recommended

### `.gitignore` contents:
```
__pycache__/
*.pyc
*.egg-info/
.scrapy/
*.log
tests/.last_result
.env
*.db
```

### File: `deploy.py` — print_deployment_summary():
```
========================================
 Deployment Complete
========================================
Repo:     https://github.com/{user}/faselhd-db
Raw API:  https://raw.githubusercontent.com/{user}/faselhd-db/main/output/series.json
Actions:  https://github.com/{user}/faselhd-db/actions
Secrets:  PAT_TOKEN, GIT_USER_EMAIL, GIT_USER_NAME ✓

Next step: connect cron-job.org (see SETUP.md)
========================================
```

---

## STEP 4 — cron-job.org Connection (SETUP.md)

Write `SETUP.md` with these exact steps:

```markdown
# Connecting cron-job.org to GitHub Actions

## What this does
cron-job.org sends an HTTP POST to GitHub every 10 minutes.
GitHub receives it and triggers the update workflow.
The workflow runs update.py, commits new items, pushes to main.

## Steps

### 1. Get your workflow dispatch URL
Replace {USER} and {REPO}:
https://api.github.com/repos/{USER}/faselhd-db/actions/workflows/update.yml/dispatches

### 2. Create cron-job.org account
Go to: https://cron-job.org → Sign up (free)

### 3. Create a new cronjob
- Title: FaselHD Auto Update
- URL: (your dispatch URL from step 1)
- Method: POST
- Headers (add each one):
    Key: Authorization      Value: Bearer {YOUR_PAT_TOKEN}
    Key: Accept             Value: application/vnd.github+json
    Key: Content-Type       Value: application/json
- Request body: {"ref":"main"}
- Schedule: every 10 minutes (select: Minutes → */10)
- Enable: ON

### 4. Test it manually
- Click "Run now" in cron-job.org dashboard
- Go to https://github.com/{USER}/faselhd-db/actions
- You should see a workflow run appear within 30 seconds
- Click it → check logs → should say "no changes" or show new items

### 5. Verify automatic runs
After 10 minutes, check Actions tab again.
A second run should appear automatically.

## Monitoring
- GitHub Actions tab shows every run with logs
- update_log.txt in the repo shows a history of every update
- Each commit message shows: "auto-update: +N items [timestamp]"

## Costs
- cron-job.org: free (up to 5 cronjobs, 1-min resolution)
- GitHub Actions: free (2,000 min/month on free tier)
  At 10-min intervals: ~4,300 runs/month × ~1 min each = ~4,300 min
  This exceeds the free tier. Use 30-minute intervals instead:
  Schedule: */30 * * * *
  Or use GitHub's native schedule (already in update.yml as fallback):
  cron: '*/30 * * * *'
```

---

## Constraints

- Zero new pip dependencies for the mock server (use stdlib only)
- The `--base-url` flag must be added to `update.py` for test overriding
- Tests must not modify the real `output/*.json` files — use temp copies
- `deploy.py` must be idempotent: if repo already exists, skip creation step
- All subprocess calls must check return codes and raise on failure

---

## Run Order

```bash
# Terminal 1 — start mock server
python tests/mock_server.py

# Terminal 2 — run tests then deploy
python tests/test_update.py
python deploy.py
```

---

## Final Deliverables Checklist

- [ ] `tests/mock_server.py` — serves fake fasel-hd.cam on localhost:8765
- [ ] `tests/test_update.py` — 8 tests, all passing
- [ ] `deploy.py` — creates repo, pushes, sets secrets
- [ ] `SETUP.md` — cron-job.org connection guide
- [ ] `update.py` supports `--base-url` and `--dry-run` flags
- [ ] `github_push.py` supports `--dry-run` flag
- [ ] All 8 tests pass before deploy runs
