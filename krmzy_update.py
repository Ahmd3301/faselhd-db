#!/usr/bin/env python3
"""Krmzy — Auto-Update (fourth source alongside FaselHD + TopCinma + Ostora).

Site: krmzy.org (Turkish series translated into Arabic).
Only Turkish series (مسلسلات تركية) are collected.

Scrapes all series from /series-list/ pages, follows each series page to count episodes,
stores in output/Krmzy/krmzy-series.json with same contract as others:
  {section_key, rank, slug, name, link, img, added_at}

Mirrors update.py/topcinma_update.py for delta/full-scrape, github_push, logging.
"""
import argparse
import gzip
import html as html_mod
import io
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(BASE_DIR, "output", "Krmzy")
OUTPUT_DIR = DEFAULT_ROOT
DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

SOURCE_TAG = "krmzy"
SECTION_KEY = "kr-series"

BASE = "https://krmzy.org"
LIST_URL = BASE + "/series-list/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en;q=0.9",
    "Accept-Encoding": "gzip",
}

SERIES_RE = re.compile(
    r'<a href="(?P<link>https://krmzy\.org/series/[^"]+)"\s+title="(?P<title>[^"]+?)"[^>]*>'
    r'.*?background-image:url\((?P<poster>[^)]+)\)',
    re.S)
EPISODE_RE = re.compile(
    r'<a href="(?P<link>https://krmzy\.org/episode/[^"]+)"\s+title="(?P<title>[^"]+?)"[^>]*>'
    r'.*?<div class="episodeNum">\s*<span>حلقة</span>\s*<span>(?P<num>\d+)</span>',
    re.S)
H1_RE = re.compile(r'<h1>([^<]+)</h1>')


def eprint(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def fetch(url, retries=4, timeout=30):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw.decode("utf-8", errors="replace")
        except Exception as err:
            last_err = err
            time.sleep(1.0 * attempt)
    raise RuntimeError(f"فشل الاتصال بـ {url}: {last_err}")


def strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(text):
    text = strip_tags(text)
    text = re.sub(r"\s*-\s*(موقع\s*)?قرمزي\s*$", "", text)
    return text.strip()


def parse_series_cards(page_html):
    cards = []
    for m in SERIES_RE.finditer(page_html):
        link = m.group("link")
        cards.append({
            "link": link,
            "title": clean_title(m.group("title")),
            "poster": m.group("poster").strip(),
        })
    return cards


def _slug_from_link(link):
    # extract slug from /series/slug/
    m = re.search(r"/series/([^/?#]+)/?", link)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    return link.rstrip("/").split("/")[-1]


def load_all_series():
    page = fetch(LIST_URL)
    series, seen = [], set()
    for card in parse_series_cards(page):
        if card["link"] not in seen:
            seen.add(card["link"])
            series.append(card)
    page_numbers = sorted({int(n) for n in re.findall(r"series-list/page/(\d+)/", page)})
    for n in page_numbers:
        try:
            body = fetch(f"{LIST_URL}page/{n}/")
        except RuntimeError:
            continue
        cards = parse_series_cards(body)
        if not cards:
            break
        for card in cards:
            if card["link"] not in seen:
                seen.add(card["link"])
                series.append(card)
        time.sleep(0.3)
    if not series:
        raise RuntimeError("لم يتم العثور على أي مسلسل في الموقع.")
    return series


def load_db():
    path = os.path.join(OUTPUT_DIR, f"{SECTION_KEY}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(data):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{SECTION_KEY}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    eprint(f"  wrote {len(data['items'])} items -> {path}")


def save_delta(items):
    if not items:
        return
    os.makedirs(DELTA_DIR, exist_ok=True)
    path = os.path.join(DELTA_DIR, f"{SECTION_KEY}.json")
    payload = {"section": SECTION_KEY, "items": items}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    eprint(f"  delta {len(items)} items -> {path}")


def update(dry_run=False):
    db = load_db()
    try:
        fetched = load_all_series()
    except Exception as e:
        return {"section": SECTION_KEY, "status": "error", "reason": str(e)[:200]}

    # Filter for Turkish series only (as requested) - site is Turkish series translated to Arabic
    # All series from krmzy.org are Turkish series; keep all as they match requirement
    items_fetched = []
    for idx, card in enumerate(fetched):
        items_fetched.append({
            "section_key": SECTION_KEY,
            "rank": 0,
            "slug": _slug_from_link(card["link"]),
            "name": card["title"],
            "img": card["poster"],
            "link": card["link"],
        })

    if db is None or not db.get("items"):
        if dry_run:
            return {"section": SECTION_KEY, "status": "full_scrape_needed", "new_count": len(items_fetched)}
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i, it in enumerate(items_fetched):
            it["rank"] = i + 1
            it["added_at"] = timestamp
        data = {"section": SECTION_KEY, "scraped_at": timestamp, "total": len(items_fetched), "items": items_fetched}
        save_db(data)
        save_delta(items_fetched)
        return {"section": SECTION_KEY, "status": "full_scrape", "new_count": len(items_fetched)}

    db_slugs = {item["slug"] for item in db["items"]}
    all_new = []
    for item in items_fetched:
        if item["slug"] not in db_slugs:
            all_new.append(item)
        else:
            break  # since list is in order, stop when see known

    if not all_new:
        return {"section": SECTION_KEY, "status": "no_changes"}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, item in enumerate(all_new):
        item["rank"] = i + 1
        item["added_at"] = timestamp
    for i, item in enumerate(db["items"]):
        item["rank"] = len(all_new) + i + 1
    db["items"] = all_new + db["items"]
    db["total"] = len(db["items"])
    db["scraped_at"] = timestamp
    if not dry_run:
        save_db(db)
        save_delta(all_new)
    return {"section": SECTION_KEY, "status": "updated", "new_count": len(all_new)}


def parse_faselhd_totals():
    log_path = os.path.join(BASE_DIR, "update_log.txt")
    if not os.path.exists(log_path):
        return None
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = [b for b in content.strip().split("\n\n") if "[faselhd]" in b]
    if not blocks:
        return None
    total_new, duration = 0, ""
    for line in blocks[-1].split("\n"):
        line = line.strip()
        m = re.match(r"Total new \(faselhd\): (\d+) items across \d+ sections", line)
        if m:
            total_new = int(m.group(1))
        m = re.match(r"Duration \(faselhd\): (\S+)", line)
        if m:
            duration = m.group(1)
    return {"total_new": total_new, "duration": duration}


def parse_block_totals(tag):
    log_path = os.path.join(BASE_DIR, "update_log.txt")
    if not os.path.exists(log_path):
        return None
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = [b for b in content.strip().split("\n\n") if f"[{tag}]" in b]
    if not blocks:
        return None
    total_new, duration = 0, ""
    for line in blocks[-1].split("\n"):
        line = line.strip()
        m = re.match(rf"Total new \({tag}\): (\d+) items across \d+ sections", line)
        if m:
            total_new = int(m.group(1))
        m = re.match(rf"Duration \({tag}\): (\S+)", line)
        if m:
            duration = m.group(1)
    return {"total_new": total_new, "duration": duration}


def write_log(results, elapsed):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"Run: {timestamp} [{SOURCE_TAG}]"]
    total_new = 0
    changed_sections = []
    for r in results:
        sec = r["section"]
        if r["status"] == "updated":
            lines.append(f"[{sec:18s}] +{r['new_count']} -> new total")
            total_new += r["new_count"]
            changed_sections.append(sec)
        elif r["status"] == "no_changes":
            lines.append(f"[{sec:18s}] +0  -> skipped")
        elif r["status"] == "full_scrape":
            lines.append(f"[{sec:18s}] full scrape -> {r['new_count']} total")
            total_new += r["new_count"]
            changed_sections.append(sec)
        elif r["status"] == "full_scrape_needed":
            lines.append(f"[{sec:18s}] full scrape needed (~{r['new_count']} items)")
            total_new += r["new_count"]
        else:
            lines.append(f"[{sec:18s}] ERROR: {r.get('reason', 'unknown')}")
    lines.append(f"Total new ({SOURCE_TAG}): {total_new} items across {len(changed_sections)} sections")
    lines.append(f"Duration ({SOURCE_TAG}): {elapsed:.0f}s")
    parts, grand = [], total_new
    for tag, label in (("faselhd", "Faselhd"), ("topcinma", "TopCinma"), ("ostora", "Ostora")):
        t = parse_block_totals(tag)
        if t is not None:
            parts.append(f"{label} {t['total_new']} ({t['duration']})")
            grand += t["total_new"]
    if parts:
        parts.append(f"Krmzy {total_new} ({elapsed:.0f}s)")
        lines.append(f"GRAND TOTAL: {grand} new items | {' + '.join(parts)}")
    lines.append("")
    with open(os.path.join(BASE_DIR, "update_log.txt"), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return changed_sections


def main():
    parser = argparse.ArgumentParser(description="Krmzy — Auto-Update")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and compare only, no writes")
    parser.add_argument("--root", type=str, default=None, help="Output root dir (default: output/Krmzy)")
    args = parser.parse_args()

    global OUTPUT_DIR, DELTA_DIR
    if args.root:
        OUTPUT_DIR = args.root if os.path.isabs(args.root) else os.path.join(BASE_DIR, args.root)
        DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

    start = time.time()
    r = update(dry_run=args.dry_run)
    elapsed = time.time() - start
    status_icon = {"updated": "+", "no_changes": "=", "full_scrape": "*",
                   "full_scrape_needed": "?", "error": "!"}.get(r["status"], "?")
    extra = f"+{r['new_count']}" if r.get("new_count") else ""
    eprint(f"  [{status_icon}] {r['section']:18s} {r['status']:20s} {extra}")

    if not args.dry_run:
        try:
            changed = write_log([r], elapsed)
            if changed and not os.environ.get("SKIP_GITHUB_PUSH"):
                eprint(f"\n{len(changed)} sections changed -> committing via github_push.py")
                subprocess.run(
                    [sys.executable, "github_push.py", "--root", OUTPUT_DIR,
                     "--sections", ",".join(changed)],
                    cwd=BASE_DIR,
                )
        except Exception as e:
            with open(os.path.join(BASE_DIR, "update_log.txt"), "a", encoding="utf-8") as f:
                f.write(f"Run: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
                        f"[{SOURCE_TAG}] FAILED: {e}\n\n")
            raise
    eprint(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
