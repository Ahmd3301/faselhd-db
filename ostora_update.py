#!/usr/bin/env python3
"""Ostora Phase 1 — Auto-Update (third source alongside FaselHD + TopCinma).

API: Ostora app API v6.2, AES-256-CBC responses, UA ostora-5.5.
10 sections (os- namespace). Stored item shape: id/name/image only
(+ pipeline mechanics). link is "" (API has no web links).

ORDERING (order-agnostic by design — API order is not trusted):
  every run fetches the FULL list per section (400/page, cheap),
  new = fetched_ids - stored_ids -> prepended with fresh rank/added_at/ord,
  stored items KEEP their stored order forever (API reshuffles are no-ops).
  A reorder_drift metric is logged for observability only.
"""
import argparse
import base64
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from bisect import bisect_left
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(BASE_DIR, "output", "Ostora")
OUTPUT_DIR = DEFAULT_ROOT
DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

SOURCE_TAG = "ostora"

BASE_CANDIDATES = [
    "https://mzqvyjnwmmx208e1srpd5nwtzka0whnxi9kbiycqgvv989kfo4.sa036.shop/api/v6.2",
]
UA = "ostora-5.5"
AES_KEY = bytes.fromhex("4e5c6d1a8b3fe8137a3b9df26a9c4de195267b8e6f6c0b4e1c3ae1d27f2b4e6f")
AES_IV = bytes.fromhex("a9c21f8d7e6b4a9db12e4f9d5c1a7b8e")

SECTIONS = [
    {"key": "os-ar-series", "wp_id": 18},
    {"key": "os-rn-series", "wp_id": 31},
    {"key": "os-arall-movies", "wp_id": 334},
    {"key": "os-ar26-movies", "wp_id": 7038},
    {"key": "os-ar25-movies", "wp_id": 104},
    {"key": "os-ar24-movies", "wp_id": 4125},
    {"key": "os-ar23-movies", "wp_id": 3800},
    {"key": "os-ar22-movies", "wp_id": 3041},
    {"key": "os-ar21-movies", "wp_id": 1650},
    {"key": "os-ar20-movies", "wp_id": 414},
]
BY_KEY = {s["key"]: s for s in SECTIONS}

MAX_PAGES = 50
PAGE_SIZE = 400


def eprint(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def decrypt_aes(b64_text):
    """Port of decryptAES() in apixa.cjs — AES-256-CBC, manual PKCS7 strip."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    raw = base64.b64decode(b64_text)
    decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    return json.loads(padded[:-padded[-1]].decode("utf-8"))


def fetch_json(url, timeout=15, retries=5):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            body = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")
            return decrypt_aes(body)
        except Exception:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def fetch_category(wp_id):
    """Full ordered id list + {id: (name, image)} for one API category."""
    order, info = [], {}
    for base in BASE_CANDIDATES:
        order, info, ok_pages = [], {}, 0
        for page in range(1, MAX_PAGES + 1):
            result = fetch_json(f"{base}/category/{wp_id}?page={page}")
            if not result:
                break
            data = result.get("data", result)
            items = data.get("items") if isinstance(data, dict) else None
            if items is None and isinstance(data, list):
                items = data
            if not items:
                break
            for ep in items:
                try:
                    eid = int(ep.get("id"))
                except (TypeError, ValueError):
                    continue
                if eid not in info:
                    order.append(eid)
                info[eid] = (str(ep.get("name", "") or "").strip(),
                             str(ep.get("image", "") or "").strip())
            ok_pages += 1
            if len(items) < PAGE_SIZE:
                break
            time.sleep(1)
        if order:
            return order, info
    return order, info


def compute_new_order(stored_slugs, fetched_slugs):
    """Pure set-diff: slugs in fetched but not stored, in fetched order."""
    known = set(stored_slugs)
    return [s for s in fetched_slugs if s not in known]


def reorder_drift(stored_slugs, fetched_pos):
    """Fraction of stored items whose relative order differs from the API.

    1 - LIS/n over API positions — O(n log n). Observability only.
    """
    seq = [fetched_pos[s] for s in stored_slugs if s in fetched_pos]
    if len(seq) < 2:
        return 0.0
    tails = []
    for x in seq:
        i = bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return 1.0 - len(tails) / len(seq)


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


def update_section(cfg, dry_run=False):
    key = cfg["key"]
    order, info = fetch_category(cfg["wp_id"])
    if not order:
        return {"section": key, "status": "error", "reason": "empty API response"}
    fetched_slugs = [str(eid) for eid in order]
    db = load_db(key)

    if db is None or not db.get("items"):
        if dry_run:
            return {"section": key, "status": "full_scrape_needed",
                    "new_count": len(fetched_slugs)}
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items = [{"section_key": key, "rank": i + 1, "slug": s,
                  "name": info[int(s)][0], "link": "",
                  "img": info[int(s)][1], "added_at": timestamp}
                 for i, s in enumerate(fetched_slugs) if info[int(s)][0]]
        data = {"section": key, "scraped_at": timestamp,
                "total": len(items), "items": items}
        save_db(key, data)
        save_delta(key, items)
        return {"section": key, "status": "full_scrape", "new_count": len(items)}

    stored_slugs = [it["slug"] for it in db["items"]]
    new_slugs = compute_new_order(stored_slugs, fetched_slugs)
    drift = reorder_drift(stored_slugs, {s: i for i, s in enumerate(fetched_slugs)})

    if not new_slugs:
        return {"section": key, "status": "no_changes", "drift": drift}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = [{"section_key": key, "rank": i + 1, "slug": s,
              "name": info[int(s)][0], "link": "",
              "img": info[int(s)][1], "added_at": timestamp}
             for i, s in enumerate(new_slugs) if info[int(s)][0]]
    for i, it in enumerate(db["items"]):
        it["rank"] = len(fresh) + i + 1
    db["items"] = fresh + db["items"]
    db["total"] = len(db["items"])
    db["scraped_at"] = timestamp
    if not dry_run:
        save_db(key, db)
        save_delta(key, fresh)
    return {"section": key, "status": "updated",
            "new_count": len(fresh), "drift": drift}


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
        extra = ""
        if r.get("drift") is not None and r["status"] in ("updated", "no_changes"):
            extra = f" (drift {r['drift']:.0%})"
        if r["status"] == "updated":
            lines.append(f"[{sec:18s}] +{r['new_count']} -> new total{extra}")
            total_new += r["new_count"]
            changed_sections.append(sec)
        elif r["status"] == "no_changes":
            lines.append(f"[{sec:18s}] +0  -> skipped{extra}")
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
    for tag, label in (("faselhd", "Faselhd"), ("topcinma", "TopCinma")):
        t = parse_block_totals(tag)
        if t is not None:
            parts.append(f"{label} {t['total_new']} ({t['duration']})")
            grand += t["total_new"]
    if parts:
        parts.append(f"Ostora {total_new} ({elapsed:.0f}s)")
        lines.append(f"GRAND TOTAL: {grand} new items | {' + '.join(parts)}")
    lines.append("")
    with open(os.path.join(BASE_DIR, "update_log.txt"), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return changed_sections


def main():
    parser = argparse.ArgumentParser(description="Ostora — Auto-Update")
    parser.add_argument("--section", type=str, default=None, help="Single section only (os-... key)")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and compare only, no writes")
    parser.add_argument("--root", type=str, default=None, help="Output root dir (default: output/Ostora)")
    args = parser.parse_args()

    global OUTPUT_DIR, DELTA_DIR
    if args.root:
        OUTPUT_DIR = args.root if os.path.isabs(args.root) else os.path.join(BASE_DIR, args.root)
        DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

    if args.section and args.section not in BY_KEY:
        eprint(f"Unknown section: {args.section}")
        sys.exit(1)
    cfgs = [BY_KEY[args.section]] if args.section else SECTIONS

    start = time.time()
    results = []
    for i, cfg in enumerate(cfgs):
        if i > 0:
            time.sleep(random.uniform(1, 3))
        try:
            r = update_section(cfg, dry_run=args.dry_run)
        except Exception as e:
            r = {"section": cfg["key"], "status": "error", "reason": str(e)[:200]}
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
