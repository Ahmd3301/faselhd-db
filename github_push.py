#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def eprint(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def git(*args, cwd=BASE_DIR):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def push_if_changed(sections, dry_run=False):
    git_email = os.environ.get("GIT_USER_EMAIL")
    git_name = os.environ.get("GIT_USER_NAME")

    if dry_run:
        eprint("[DRY-RUN] Would configure git:")
        eprint(f"  git config user.email {git_email or '<GIT_USER_EMAIL env>'}")
        eprint(f"  git config user.name  {git_name or '<GIT_USER_NAME env>'}")
    else:
        if git_email:
            git("config", "user.email", git_email)
        if git_name:
            git("config", "user.name", git_name)

    if not sections:
        if dry_run:
            eprint("[DRY-RUN] No sections changed, nothing to commit")
        return False

    paths = [f"output/{s}.json" for s in sections] + ["update_log.txt"]

    if dry_run:
        eprint("[DRY-RUN] Would run:")
        eprint(f"  git add {' '.join(paths)}")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        eprint(f'  git commit -m "auto-update: {",".join(sections)} [{timestamp}]"')
        eprint("  git push")
        return True

    result = git("add", "--", *paths)
    if result.returncode != 0:
        eprint(f"git add failed: {result.stderr}")
        sys.exit(1)

    result = git("diff", "--cached", "--quiet")
    if result.returncode == 0:
        eprint("Nothing staged -- skipping commit")
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"auto-update: {','.join(sections)} [{timestamp}]"
    result = git("commit", "-m", msg)
    if result.returncode != 0:
        eprint(f"git commit failed: {result.stderr}")
        sys.exit(1)

    result = git("push")
    if result.returncode != 0:
        eprint(f"git push failed: {result.stderr}")
        sys.exit(1)

    eprint(f"Pushed: {msg}")
    return True


def main():
    parser = argparse.ArgumentParser(description="FaselHD -- git commit & push")
    parser.add_argument("--sections", type=str, default="", help="Comma-separated changed sections")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    args = parser.parse_args()

    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    push_if_changed(sections, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
