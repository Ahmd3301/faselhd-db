#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

SECTIONS = [
    "movies", "series", "anime", "asian-series",
    "asian-movies", "hindi", "anime-movies", "tvshows",
]


def eprint(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def load_db(section):
    path = os.path.join(OUTPUT_DIR, f"{section}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(section, data):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{section}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    eprint(f"  wrote {len(data['items'])} items -> {path}")


def _run_spider(spider_name, section, max_pages, base_url, extra_settings=None, page=None):
    with tempfile.NamedTemporaryFile(suffix=".jl", delete=False, dir=BASE_DIR) as tmp:
        tmp_path = tmp.name

    args = [
        sys.executable, "-m", "scrapy", "crawl", spider_name,
        "-a", f"section={section}",
        "-a", f"max_pages={max_pages}",
        "-a", f"base_url={base_url}",
        "-o", tmp_path,
        "-s", "LOG_LEVEL=ERROR",
    ]
    if page:
        args.extend(["-a", f"page={page}"])
    if extra_settings:
        for k, v in extra_settings.items():
            args.extend(["-s", f"{k}={v}"])

    result = subprocess.run(args, cwd=BASE_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        eprint(f"  spider {spider_name} failed: {result.stderr}")
        return []

    items = []
    if os.path.exists(tmp_path):
        with open(tmp_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        os.unlink(tmp_path)
    return items


def fetch_page(section, page_num, base_url):
    return _run_spider("faselhd_update", section, max_pages=1, base_url=base_url, page=page_num)


def _run_full_scrape(section, base_url):
    items = _run_spider("faselhd", section, max_pages=9999, base_url=base_url,
                        extra_settings={"LOG_LEVEL": "ERROR"})
    return len(items)


def update_section(section, base_url, dry_run):
    db = load_db(section)

    if db is None or not db.get("items"):
        if dry_run:
            items = fetch_page(section, 1, base_url)
            return {"section": section, "status": "full_scrape_needed", "new_count": len(items)}
        count = _run_full_scrape(section, base_url)
        return {"section": section, "status": "full_scrape", "new_count": count}

    db_slugs = {item["slug"] for item in db["items"]}
    all_new_items = []
    page = 1

    while True:
        items = fetch_page(section, page, base_url)

        if not items:
            break

        page_new = []
        for item in items:
            if item["slug"] in db_slugs:
                break
            page_new.append(item)

        all_new_items.extend(page_new)

        if page_new:
            if len(page_new) < len(items):
                break
            if len(items) < 24:
                break
        page += 1

    if not all_new_items:
        return {"section": section, "status": "no_changes"}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for i, item in enumerate(all_new_items):
        item["rank"] = i + 1
        item["added_at"] = timestamp

    for i, item in enumerate(db["items"]):
        item["rank"] = len(all_new_items) + i + 1

    db["items"] = all_new_items + db["items"]
    db["total"] = len(db["items"])
    db["scraped_at"] = timestamp

    if not dry_run:
        save_db(section, db)

    return {"section": section, "status": "updated", "new_count": len(all_new_items)}


def write_log(results, elapsed):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"Run: {timestamp}"]
    total_new = 0
    changed_sections = []

    for r in results:
        sec = r["section"]
        if r["status"] == "updated":
            lines.append(f"[{sec:15s}] +{r['new_count']} -> new total")
            total_new += r["new_count"]
            changed_sections.append(sec)
        elif r["status"] == "no_changes":
            lines.append(f"[{sec:15s}] +0  -> skipped")
        elif r["status"] == "full_scrape":
            lines.append(f"[{sec:15s}] full scrape -> {r['new_count']} total")
            total_new += r["new_count"]
            changed_sections.append(sec)
        elif r["status"] == "full_scrape_needed":
            lines.append(f"[{sec:15s}] full scrape needed (~{r['new_count']} items)")
            total_new += r["new_count"]
        else:
            lines.append(f"[{sec:15s}] ERROR: {r.get('reason', 'unknown')}")

    lines.append(f"Total new: {total_new} items across {len(changed_sections)} sections")
    lines.append(f"Duration: {elapsed:.0f}s")
    lines.append("")

    log_path = os.path.join(BASE_DIR, "update_log.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return changed_sections


def main():
    parser = argparse.ArgumentParser(description="FaselHD Phase 2 — Auto-Update")
    parser.add_argument("--section", type=str, default=None, help="Single section only")
    parser.add_argument("--base-url", type=str, default=None, help="Override base URL (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and compare only, no writes")
    args = parser.parse_args()

    base_url = args.base_url or "https://www.fasel-hd.cam"
    sections = [args.section] if args.section else SECTIONS

    start = time.time()
    results = []

    for sec in sections:
        r = update_section(sec, base_url=base_url, dry_run=args.dry_run)
        results.append(r)
        status_icon = {"updated": "+", "no_changes": "=", "full_scrape": "*",
                       "full_scrape_needed": "?", "error": "!"}.get(r["status"], "?")
        extra = f"+{r['new_count']}" if r.get("new_count") else ""
        eprint(f"  [{status_icon}] {sec:15s} {r['status']:20s} {extra}")

    elapsed = time.time() - start

    if not args.dry_run:
        changed = write_log(results, elapsed)
        if changed:
            eprint(f"\n{len(changed)} sections changed -> committing via github_push.py")
            subprocess.run(
                [sys.executable, "github_push.py", "--sections", ",".join(changed)],
                cwd=BASE_DIR,
            )

    eprint(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
