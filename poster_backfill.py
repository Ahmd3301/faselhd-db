#!/usr/bin/env python3
"""Backfill: host every existing TopCinma (tc-*) / Ostora (os-*) poster.

Parallel download -> batched upload (only missing blobs) -> single deployment.
Updates poster_ws/poster_cf in the section JSONs and imagecdn/manifest.json,
commits & pushes, optionally sends a Telegram summary, then removes the staged
images.

Run locally with dry-run first:
    python poster_backfill.py --dry-run
"""
import argparse
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import cf_pages
from poster_common import (
    BASE_DIR, MANIFEST_PATH, PAGES_PROJECT, POSTER_ROOTS,
    commit_and_push, eprint, is_poster_section,
    load_json, load_manifest, save_json, save_manifest, stage_asset,
)

MAX_MANIFEST_PATHS = 19900  # Pages Free: 20,000 files per deployment


def gather_targets(sections_filter):
    files, targets = {}, []
    for root in POSTER_ROOTS:
        if not os.path.isdir(root):
            continue
        for fname in sorted(os.listdir(root)):
            if not fname.endswith(".json"):
                continue
            section = fname[:-5]
            if not is_poster_section(section):
                continue
            if sections_filter and section not in sections_filter:
                continue
            path = os.path.join(root, fname)
            data = load_json(path)
            files[path] = data
            for it in data.get("items", []):
                img = (it.get("img") or "").strip()
                if not img or it.get("poster_cf"):
                    continue
                targets.append((path, it, section, img))
    return files, targets


def send_telegram(text):
    import json as _json
    import urllib.request
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        eprint("Telegram: missing token/chat, skipping")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = _json.dumps({"chat_id": int(chat), "text": text,
                        "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        eprint("Telegram notification sent")
    except Exception as e:  # noqa: BLE001
        eprint(f"Telegram error: {e}")


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main():
    ap = argparse.ArgumentParser(description="Backfill tc-/os- posters to Pages")
    ap.add_argument("--sections", default="", help="Comma list of sections to process")
    ap.add_argument("--limit", type=int, default=0, help="Max items (debug)")
    ap.add_argument("--workers", type=int, default=16, help="Download concurrency")
    ap.add_argument("--batch-size", type=int, default=1000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()

    sections_filter = {s.strip() for s in args.sections.split(",") if s.strip()}
    files, targets = gather_targets(sections_filter)
    if args.limit:
        targets = targets[:args.limit]
    eprint(f"Files: {len(files)} | items needing a poster: {len(targets)}")
    if args.dry_run:
        counts = {}
        for _, _, section, _ in targets:
            counts[section] = counts.get(section, 0) + 1
        for sec in sorted(counts):
            eprint(f"  {sec:20s} {counts[sec]}")
        return
    if not targets:
        eprint("Nothing to backfill")
        return

    manifest = load_manifest()
    before = len(manifest)
    stage_dir = tempfile.mkdtemp(prefix="posters_stage_")
    uploaded_total = 0
    failed = 0
    started = time.time()

    try:
        done = 0
        for batch in chunks(targets, args.batch_size):
            assets = []

            def work(t):
                _, _, section, img = t
                return t, stage_asset(img, section, stage_dir)

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for t, asset in pool.map(work, batch):
                    item = t[1]
                    if asset is None:
                        item["poster_ws"] = t[3]
                        failed += 1
                        continue
                    item["poster_ws"] = asset["ws"]
                    item["poster_cf"] = asset["cf"]
                    manifest.setdefault(asset["remote"], asset["hash"])
                    assets.append(asset)

            if assets:
                uploaded_total += cf_pages.upload_only(PAGES_PROJECT, assets)
                for a in assets:
                    try:
                        os.remove(a["file"])
                    except OSError:
                        pass

            done += len(batch)
            eprint(f"  {done}/{len(targets)} processed | uploaded={uploaded_total} "
                   f"failed={failed} manifest={len(manifest)}")

        added = len(manifest) - before
        if len(manifest) > MAX_MANIFEST_PATHS:
            eprint(f"WARNING: manifest has {len(manifest)} paths "
                   f"(>= {MAX_MANIFEST_PATHS}); deployment may fail on free plan")

        if added > 0 and not args.no_deploy:
            dep = cf_pages.deploy_only(PAGES_PROJECT, manifest)
            eprint(f"Deployed: {dep.get('url')}")

        save_manifest(manifest)
        for path, data in files.items():
            save_json(path, data)

        if not args.no_push:
            changed = sorted({os.path.relpath(MANIFEST_PATH, BASE_DIR)} |
                             {os.path.relpath(p, BASE_DIR) for p in files})
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            commit_and_push(changed, f"posters: backfill {added} assets [tc/os] [{ts}]")
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    elapsed = time.time() - started
    summary = (f"🖼 Poster backfill done\n"
               f"• items: {len(targets)}\n"
               f"• new assets: {len(manifest) - before}\n"
               f"• uploaded: {uploaded_total}\n"
               f"• failed downloads: {failed}\n"
               f"• manifest total: {len(manifest)}\n"
               f"• time: {elapsed:.0f}s")
    eprint(summary)
    if args.notify:
        send_telegram(summary)


if __name__ == "__main__":
    main()
