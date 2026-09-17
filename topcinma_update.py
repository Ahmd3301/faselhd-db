#!/usr/bin/env python3
"""TopCinma Phase 1 — Auto-Update (mirrors update.py for FaselHD).

Sections (tc- namespace):
  REST movies : tc-movies-foreign (wp 3), tc-movies-asian (4), tc-anime-movies (5)
  HTML series : tc-series-foreign (7), tc-series-anime (8), tc-series-asian (9)

Stored item shape — name + link + poster (same contract as FaselHD):
  {section_key, rank, slug, name, link, img, added_at}
Poster sources (same as bot.js): REST _embedded wp:featuredmedia (cover.jpg
filtered out) for movies, .Small--Box img data-src/src for series lists.
slug is derived from link and is the dedup key.
"""
import argparse
import html as htmlmod
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse, urlunparse, unquote, urljoin, quote
from datetime import datetime, timezone

import parsel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(BASE_DIR, "output", "TopCinma")
OUTPUT_DIR = DEFAULT_ROOT
DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

SOURCE_TAG = "topcinma"

REST_BASES = [
    "https://web.topcinemaa.com/wp-json/wp/v2",
    "https://topcinema.io/wp-json/wp/v2",
]
SITE_BASES = [
    "https://topcinemaa.top",
    "https://topcinema.io",
]

SECTIONS = [
    {"key": "tc-movies-foreign", "kind": "rest", "wp_id": 3},
    {"key": "tc-movies-asian", "kind": "rest", "wp_id": 4},
    {"key": "tc-anime-movies", "kind": "rest", "wp_id": 5},
    {"key": "tc-series-foreign", "kind": "html", "wp_id": 7,
     "cat_slug": "%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D8%AC%D9%86%D8%A8%D9%8A"},
    {"key": "tc-series-anime", "kind": "html", "wp_id": 8,
     "cat_slug": "%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D9%86%D9%85%D9%8A"},
    {"key": "tc-series-asian", "kind": "html", "wp_id": 9,
     "cat_slug": "%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D8%B3%D9%8A%D9%88%D9%8A%D8%A9"},
]
BY_KEY = {s["key"]: s for s in SECTIONS}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REST_PER_PAGE = 100
REST_MAX_PAGES = 20      # delta mode cap (full mode: uncapped)
HTML_MAX_PAGES = 100     # delta mode cap (full mode: 300)


def eprint(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def pick_poster(url):
    """Poster URL or '' — drops placeholder cover.jpg (same rule as bot.js)."""
    u = (url or "").strip()
    return "" if (not u or "cover.jpg" in u) else u


def clean_title(raw):
    """Port of cleanTitle() in bot.js — name only, no metadata."""
    t = re.sub(r"<[^>]+>", "", raw or "")
    t = htmlmod.unescape(t)
    t = re.sub(r"فيلم\s+", "", t)
    t = re.sub(r"مسلسل\s+", "", t)
    t = re.sub(r"\s+مترجم\s+اون\s+لاين$", "", t)
    t = re.sub(r"\s+مترجمة$", "", t)
    t = re.sub(r"\s+مترجم$", "", t)
    return t.strip()


def http_get(url, timeout=30, retries=5):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return None, {}
        except Exception:
            if attempt < retries:
                time.sleep(2)
                continue
            return None, {}
    return None, {}


def rest_get(path, params):
    from urllib.parse import urlencode
    qs = urlencode(params)
    for base in REST_BASES:
        body, headers = http_get(f"{base}{path}?{qs}")
        if body is not None:
            try:
                return json.loads(body), headers
            except Exception:
                continue
    return None, {}


def site_get(path):
    if not path.startswith("/"):
        path = "/" + path
    for base in SITE_BASES:
        body, _ = http_get(base + path)
        if body:
            return body
    return ""


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


def save_delta(section, items):
    if not items:
        return
    os.makedirs(DELTA_DIR, exist_ok=True)
    path = os.path.join(DELTA_DIR, f"{section}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"section": section, "items": items}, f, ensure_ascii=False)
    eprint(f"  delta {len(items)} items -> {path}")


def fetch_rest_page(wp_id, page, per_page=REST_PER_PAGE):
    data, headers = rest_get("/posts", {
        "categories": wp_id, "per_page": per_page, "page": page,
        "_embed": 1, "orderby": "date", "order": "desc",
    })
    if not data:
        return [], 0
    items = []
    for post in data:
        slug = unquote(str(post.get("slug", "")).strip())
        name = clean_title(post.get("title", {}).get("rendered", ""))
        link = str(post.get("link", "")).strip()
        if not slug or not name or not link:
            continue
        poster = ""
        try:
            media = (post.get("_embedded", {}) or {}).get("wp:featuredmedia") or []
            if media:
                poster = pick_poster(media[0].get("source_url"))
        except Exception:
            poster = ""
        items.append({"slug": slug, "name": name, "link": link, "img": poster})
    total_pages = 0
    for k in ("X-Wp-Totalpages", "x-wp-totalpages"):
        if k in headers:
            try:
                total_pages = int(headers[k])
            except ValueError:
                pass
    return items, total_pages


def fetch_html_page(cat_slug, page_num):
    path = f"/category/{cat_slug}/" if page_num <= 1 else f"/category/{cat_slug}/page/{page_num}/"
    html = site_get(path)
    if not html:
        return [], 1
    sel = parsel.Selector(text=html)
    items = []
    for a in sel.css(".Small--Box a"):
        href = (a.attrib.get("href") or "").strip()
        m = re.search(r"/series/([^/]+)/?$", href)
        if not m:
            continue
        slug = unquote(m.group(1))
        name = clean_title(" ".join(a.css("h3.title ::text").getall()).strip())
        if not slug or not name:
            continue
        poster = pick_poster(
            a.css("img::attr(data-src)").get()
            or a.css("img::attr(src)").get() or "")
        link = href if href.startswith("http") else urljoin(SITE_BASES[0] + "/", href)
        items.append({"slug": slug, "name": name, "link": link, "img": poster})
    total_pages = 1
    for txt in sel.css("ul.page-numbers a ::text").getall():
        try:
            n = int(txt.strip())
            total_pages = max(total_pages, n)
        except ValueError:
            pass
    return items, total_pages


def stamp_new(items):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, it in enumerate(items):
        it["added_at"] = timestamp
    return timestamp


def update_rest_section(cfg, dry_run, full):
    key = cfg["key"]
    db = load_db(key)
    if db is None or not db.get("items"):
        if full or dry_run:
            items, _ = fetch_rest_page(cfg["wp_id"], 1)
            if dry_run:
                return {"section": key, "status": "full_scrape_needed", "new_count": len(items)}
        # full scrape: all pages
        all_items, seen = [], set()
        page, total_pages = 1, 1 << 30
        while page <= total_pages and page <= 500:
            items, total_pages = fetch_rest_page(cfg["wp_id"], page)
            if not items:
                break
            for it in items:
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    all_items.append(it)
            page += 1
            time.sleep(2)
        if dry_run:
            return {"section": key, "status": "full_scrape_needed", "new_count": len(all_items)}
        timestamp = stamp_new(all_items)
        for i, it in enumerate(all_items):
            it.update({"section_key": key, "rank": i + 1})
        data = {"section": key, "scraped_at": timestamp,
                "total": len(all_items), "items": all_items}
        save_db(key, data)
        save_delta(key, all_items)
        return {"section": key, "status": "full_scrape", "new_count": len(all_items)}

    known = {it["slug"] for it in db["items"]}
    fresh, page = [], 1
    while page <= REST_MAX_PAGES:
        items, _ = fetch_rest_page(cfg["wp_id"], page)
        if not items:
            break
        stop = False
        for it in items:
            if it["slug"] in known:
                stop = True
                break
            fresh.append(it)
        if stop or len(items) < REST_PER_PAGE:
            break
        page += 1
        time.sleep(2)

    if not fresh:
        return {"section": key, "status": "no_changes"}
    timestamp = stamp_new(fresh)
    for i, it in enumerate(fresh):
        it.update({"section_key": key, "rank": i + 1})
    for i, it in enumerate(db["items"]):
        it["rank"] = len(fresh) + i + 1
    db["items"] = fresh + db["items"]
    db["total"] = len(db["items"])
    db["scraped_at"] = timestamp
    if not dry_run:
        save_db(key, db)
        save_delta(key, fresh)
    return {"section": key, "status": "updated", "new_count": len(fresh)}


def update_html_section(cfg, dry_run, full):
    key = cfg["key"]
    db = load_db(key)
    cap = 300 if (full and db is None) else HTML_MAX_PAGES
    if db is None or not db.get("items"):
        if dry_run:
            items, _ = fetch_html_page(cfg["cat_slug"], 1)
            return {"section": key, "status": "full_scrape_needed", "new_count": len(items)}
        all_items, seen, page = [], set(), 1
        while page <= cap:
            items, total_pages = fetch_html_page(cfg["cat_slug"], page)
            if not items:
                break
            for it in items:
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    all_items.append(it)
            if page >= total_pages:
                break
            page += 1
            time.sleep(1)
        timestamp = stamp_new(all_items)
        for i, it in enumerate(all_items):
            it.update({"section_key": key, "rank": i + 1})
        data = {"section": key, "scraped_at": timestamp,
                "total": len(all_items), "items": all_items}
        save_db(key, data)
        save_delta(key, all_items)
        return {"section": key, "status": "full_scrape", "new_count": len(all_items)}

    known = {it["slug"] for it in db["items"]}
    fresh, page = [], 1
    while page <= HTML_MAX_PAGES:
        items, _ = fetch_html_page(cfg["cat_slug"], page)
        if not items:
            break
        stop = False
        for it in items:
            if it["slug"] in known:
                stop = True
                break
            fresh.append(it)
        if stop:
            break
        page += 1
        time.sleep(1)

    if not fresh:
        return {"section": key, "status": "no_changes"}
    timestamp = stamp_new(fresh)
    for i, it in enumerate(fresh):
        it.update({"section_key": key, "rank": i + 1})
    for i, it in enumerate(db["items"]):
        it["rank"] = len(fresh) + i + 1
    db["items"] = fresh + db["items"]
    db["total"] = len(db["items"])
    db["scraped_at"] = timestamp
    if not dry_run:
        save_db(key, db)
        save_delta(key, fresh)
    return {"section": key, "status": "updated", "new_count": len(fresh)}


def update_section(cfg, dry_run=False, full=False):
    try:
        if cfg["kind"] == "rest":
            return update_rest_section(cfg, dry_run, full)
        return update_html_section(cfg, dry_run, full)
    except Exception as e:
        return {"section": cfg["key"], "status": "error", "reason": str(e)[:200]}


def parse_faselhd_totals():
    """Read latest [faselhd] block totals from the shared log for GRAND TOTAL."""
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
    fas = parse_faselhd_totals()
    if fas is not None:
        grand = fas["total_new"] + total_new
        lines.append(f"GRAND TOTAL: {grand} new items | Faselhd {fas['total_new']} ({fas['duration']}) "
                     f"+ TopCinma {total_new} ({elapsed:.0f}s)")
    lines.append("")
    with open(os.path.join(BASE_DIR, "update_log.txt"), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return changed_sections


def main():
    parser = argparse.ArgumentParser(description="TopCinma — Auto-Update")
    parser.add_argument("--section", type=str, default=None, help="Single section only (tc-... key)")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and compare only, no writes")
    parser.add_argument("--full", action="store_true", help="Force full scrape for missing sections")
    parser.add_argument("--root", type=str, default=None, help="Output root dir (default: output/TopCinma)")
    args = parser.parse_args()

    global OUTPUT_DIR, DELTA_DIR
    if args.root:
        OUTPUT_DIR = args.root if os.path.isabs(args.root) else os.path.join(BASE_DIR, args.root)
        DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

    cfgs = [BY_KEY[args.section]] if args.section else SECTIONS
    if args.section and args.section not in BY_KEY:
        eprint(f"Unknown section: {args.section}")
        sys.exit(1)

    start = time.time()
    results = []
    for i, cfg in enumerate(cfgs):
        if i > 0:
            time.sleep(random.uniform(3, 6))
        r = update_section(cfg, dry_run=args.dry_run, full=args.full)
        results.append(r)
        status_icon = {"updated": "+", "no_changes": "=", "full_scrape": "*",
                       "full_scrape_needed": "?", "error": "!"}.get(r["status"], "?")
        extra = f"+{r['new_count']}" if r.get("new_count") else ""
        eprint(f"  [{status_icon}] {cfg['key']:18s} {r['status']:20s} {extra}")

    elapsed = time.time() - start
    if not args.dry_run:
        try:
            changed = write_log(results, elapsed)
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
