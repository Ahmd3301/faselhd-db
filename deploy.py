#!/usr/bin/env python3
"""FaselHD Phase 2 — Deploy to GitHub."""

import getpass
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GITIGNORE_CONTENT = """__pycache__/
*.pyc
*.egg-info/
.scrapy/
*.log
tests/.last_result
.env
*.db
"""


def tests_passed():
    result_path = os.path.join(BASE_DIR, "tests", ".last_result")
    if not os.path.exists(result_path):
        return False
    with open(result_path) as f:
        data = json.load(f)
    return data.get("passed") is True and data.get("total", 0) == 8


def run_git(*args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=BASE_DIR, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr}")
        sys.exit(1)
    return result


def create_gitignore():
    path = os.path.join(BASE_DIR, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(GITIGNORE_CONTENT)
        print("  .gitignore created")


def get_total_items():
    total = 0
    output_dir = os.path.join(BASE_DIR, "output")
    if os.path.isdir(output_dir):
        for fname in os.listdir(output_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(output_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    total += data.get("total", 0)
                except Exception:
                    pass
    return total


def main():
    print("=" * 60)
    print(" FaselHD Phase 2 — Deploy")
    print("=" * 60)

    # Check tests
    if not tests_passed():
        print("ERROR: Tests must pass first.")
        print("  Run: python tests/test_update.py")
        print("  Then re-run this script.")
        sys.exit(1)

    print("  Tests passed OK")

    # Check gh CLI
    try:
        subprocess.run(
            ["gh", "--version"],
            capture_output=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: GitHub CLI (gh) not found or not working.")
        print("  Install from: https://cli.github.com/")
        sys.exit(1)

    # Get GitHub username
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True, text=True, check=True
    )
    gh_username = result.stdout.strip()
    repo_name = "faselhd-db"
    print(f"  GitHub user: {gh_username}")

    # Create repo
    repo_full = f"{gh_username}/{repo_name}"
    result = subprocess.run(
        ["gh", "repo", "create", repo_full,
         "--public",
         "--description", f"Auto-updated database of FaselHD content ({get_total_items():,}+ items)",
         "--confirm"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  Repo created: {repo_full}")
    elif "already exists" in result.stderr:
        print(f"  Repo already exists: {repo_full} (skipped)")
    else:
        print(f"  ERROR creating repo: {result.stderr}")
        sys.exit(1)

    # Init git locally if needed
    if not os.path.exists(os.path.join(BASE_DIR, ".git")):
        run_git("init")
        run_git("branch", "-M", "main")
        print("  Git initialized")

    # Add remote
    remote_url = f"https://github.com/{repo_full}.git"
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=BASE_DIR, capture_output=True, text=True
    )
    if result.returncode != 0:
        run_git("remote", "add", "origin", remote_url)
        print(f"  Remote added: {remote_url}")
    else:
        print(f"  Remote already exists: {result.stdout.strip()}")

    # .gitignore
    create_gitignore()

    # Initial commit
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = get_total_items()

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=BASE_DIR, capture_output=True, text=True
    )
    if result.stdout.strip():
        run_git("add", ".")
        commit_msg = f"initial: {total} items across 8 sections | {timestamp}"
        run_git("commit", "-m", commit_msg)
        print(f"  Committed: {commit_msg}")
    else:
        print("  Nothing new to commit")

    # Push
    run_git("push", "-u", "origin", "main")
    print("  Pushed to main")

    # Secrets
    print()
    print("Enter GitHub secrets (input hidden):")
    pat = getpass.getpass("  GitHub PAT (repo+workflow scopes): ")
    git_email = getpass.getpass("  Git commit email: ")
    git_name = getpass.getpass("  Git commit name: ")

    for key, value in [("PAT_TOKEN", pat), ("GIT_USER_EMAIL", git_email), ("GIT_USER_NAME", git_name)]:
        result = subprocess.run(
            ["gh", "secret", "set", key, "--body", value, "--repo", repo_full],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  Secret {key} set")
        else:
            print(f"  ERROR setting {key}: {result.stderr}")

    # Summary
    print()
    print("=" * 60)
    print(" Deployment Complete")
    print("=" * 60)
    print(f"  Repo:    https://github.com/{gh_username}/{repo_name}")
    print(f"  Raw API: https://raw.githubusercontent.com/{gh_username}/{repo_name}/main/output/series.json")
    print(f"  Actions: https://github.com/{gh_username}/{repo_name}/actions")
    print(f"  Secrets: PAT_TOKEN, GIT_USER_EMAIL, GIT_USER_NAME OK")
    print()
    print("  Next step: connect cron-job.org (see SETUP.md)")
    print("=" * 60)


if __name__ == "__main__":
    main()
