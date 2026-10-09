#!/usr/bin/env python3
"""Incremental poster hosting for TopCinma (tc-*) and Ostora (os-*).

Run after the scrapers: it reads only the new items from output/*/.delta,
downloads their posters, uploads just the missing assets to Cloudflare Pages,
updates imagecdn/manifest.json, and writes poster_ws/poster_cf back into the
main section JSONs (committed) and the .delta copies (for supabase_push).

A deployment is created only when new assets actually appear, keeping the
500-deployments/month Pages quota safe.
"""
import os
from datetime import datetime, timezone

import cf_pages
from poster_common import (
    BASE_DIR, MANIFEST_PATH, PAGES_PROJECT, POSTER_ROOTS,
    build_asset, commit_and_push, eprint, is_poster_section,
    load_json, load_manifest, save_json, save_manifest,
)


def process_delta(delta_path, section, manifest, new_assets, main_updates):
    data = load_json(delta_path)
    root = os.path.dirname(os.path.dirname(delta_path))
    main_path = os.path.join(root, os.path.basename(delta_path))
    main_data = main_updates.get(main_path)
    if main_data is None and os.path.exists(main_path):
        main_data = load_json(main_path)
    main_index = {}
    if main_data is not None:
        main_index = {it.get("slug"): it for it in main_data.get("items", [])}

    delta_changed = False
    for it in data.get("items", []):
        img = (it.get("img") or "").strip()
        if not img or it.get("poster_cf"):
            continue
        asset = build_asset(img, section)
        it["poster_ws"] = img
        delta_changed = True
        target = main_index.get(it.get("slug"))
        if target is not None:
            target["poster_ws"] = img
        if asset is None:
            eprint(f"    WARN download failed: {img}")
            continue
        it["poster_cf"] = asset["cf"]
        if target is not None:
            target["poster_cf"] = asset["cf"]
        if asset["remote"] not in manifest:
            manifest[asset["remote"]] = asset["hash"]
            new_assets.append(asset)

    if delta_changed:
        save_json(delta_path, data)
    if main_data is not None and main_index:
        main_updates[main_path] = main_data


def main():
    manifest = load_manifest()
    manifest_before = dict(manifest)
    new_assets = []

    changed_files = []
    dirty_main = {}

    for root in POSTER_ROOTS:
        delta_dir = os.path.join(root, ".delta")
        if not os.path.isdir(delta_dir):
            continue
        for fname in sorted(os.listdir(delta_dir)):
            if not fname.endswith(".json"):
                continue
            section = fname[:-5]
            if not is_poster_section(section):
                continue
            delta_path = os.path.join(delta_dir, fname)
            eprint(f"  [{section}] scanning delta")
            process_delta(delta_path, section, manifest, new_assets, dirty_main)

    added = len(manifest) - len(manifest_before)
    if added <= 0 and not dirty_main:
        eprint("No new posters -- nothing to host")
        return

    if added > 0:
        eprint(f"Uploading {len(new_assets)} asset(s); manifest {len(manifest)} paths")
        dep, uploaded = cf_pages.deploy(PAGES_PROJECT, manifest, new_assets)
        eprint(f"  uploaded={uploaded} deployment={dep.get('url')}")

    save_manifest(manifest)
    for path, data in dirty_main.items():
        save_json(path, data)

    changed_files = sorted({
        os.path.relpath(p, BASE_DIR)
        for p in [MANIFEST_PATH] + list(dirty_main.keys())
    })

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not os.environ.get("SKIP_GITHUB_PUSH"):
        commit_and_push(
            changed_files,
            f"posters: host {added} new asset(s) [tc/os] [{ts}]",
        )
    eprint("Done")


if __name__ == "__main__":
    main()
