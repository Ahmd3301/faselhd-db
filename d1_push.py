#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

DB_NAME = os.environ.get("D1_DATABASE_NAME", "faselhd-db")
STATEMENTS_PER_CHUNK = 500


def eprint(*args, **kwargs):
    print(*args, flush=True, **kwargs)


def find_wrangler():
    wrangler = shutil.which("wrangler")
    if wrangler:
        return [wrangler]
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        return [npx, "wrangler"]
    eprint("ERROR: wrangler not found in PATH")
    sys.exit(1)


def sql_escape(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def item_values(section, item):
    return "(%s, %s, %s, %s, %s, %s)" % (
        sql_escape(section),
        sql_escape(item.get("slug")),
        sql_escape(item.get("name")),
        sql_escape(item.get("img")),
        sql_escape(item.get("link")),
        sql_escape(item.get("added_at")),
    )


def load_section(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def collect_items(full_mode):
    sections = {}
    if full_mode:
        for fname in sorted(os.listdir(OUTPUT_DIR)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(OUTPUT_DIR, fname)
            data = load_section(path)
            items = data.get("items") or []
            key = data.get("section") or fname[:-5]
            sections[key] = items
    else:
        if not os.path.isdir(DELTA_DIR):
            return None
        for fname in sorted(os.listdir(DELTA_DIR)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(DELTA_DIR, fname)
            data = load_section(path)
            items = data.get("items") or []
            key = data.get("section") or fname[:-5]
            sections[key] = items
    return sections


def build_chunks(sections, tmp_dir):
    chunk_paths = []
    buffer = []

    def flush():
        nonlocal buffer
        if not buffer:
            return
        if sections:
            changed = ",".join(sql_escape(s) for s in sections)
            buffer.append(
                "UPDATE sections SET items_count = "
                "(SELECT COUNT(*) FROM items WHERE items.section_key = sections.key) "
                "WHERE key IN (%s);" % changed
            )
        path = os.path.join(tmp_dir, "chunk_%03d.sql" % len(chunk_paths))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(buffer))
        chunk_paths.append(path)
        eprint("  built %s (%d statements)" % (os.path.basename(path), len(buffer)))
        buffer = []

    for section, items in sections.items():
        for item in items:
            if not item.get("slug"):
                continue
            buffer.append(
                "INSERT OR IGNORE INTO items (section_key, slug, name, img, link, added_at) "
                "VALUES %s;" % item_values(section, item)
            )
            if len(buffer) >= STATEMENTS_PER_CHUNK:
                flush()
    flush()
    return chunk_paths


def execute_chunks(chunk_paths, wrangler_cmd):
    for path in chunk_paths:
        cmd = wrangler_cmd + [
            "d1", "execute", DB_NAME,
            "--remote",
            "-y",
            "--json",
            "--file=" + path,
        ]
        result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        payload = None
        try:
            start = result.stdout.find("[")
            payload = json.loads(result.stdout[start:]) if start != -1 else None
        except Exception:
            payload = None
        ok = result.returncode == 0 and payload is not None and all(r.get("success") for r in payload)
        if not ok:
            eprint("ERROR executing %s" % os.path.basename(path))
            eprint(result.stdout[-2000:])
            eprint(result.stderr[-2000:])
            return False
        writes = sum(r.get("meta", {}).get("rows_written", 0) for r in payload)
        eprint("  executed %s OK (rows_written=%d)" % (os.path.basename(path), writes))
    return True


def main():
    parser = argparse.ArgumentParser(description="Push FaselHD data to Cloudflare D1")
    parser.add_argument("--full", action="store_true", help="Full backfill from output/*.json instead of .delta files")
    parser.add_argument("--keep-delta", action="store_true", help="Do not delete delta files after success")
    args = parser.parse_args()

    sections = collect_items(args.full)
    if sections is None or not any(sections.values()):
        eprint("No data to push%s, exiting" % (" (--full)" if args.full else ": no .delta files"))
        return

    total = sum(len(v) for v in sections.values())
    eprint("Mode: %s | sections=%d | items=%d" % (
        "FULL" if args.full else "DELTA", len(sections), total))

    wrangler_cmd = find_wrangler()
    tmp_dir = tempfile.mkdtemp(prefix="d1push_")
    try:
        chunk_paths = build_chunks(sections, tmp_dir)
        if not chunk_paths:
            eprint("Nothing to push after filtering, exiting")
            return
        if not execute_chunks(chunk_paths, wrangler_cmd):
            eprint("FAILED: delta files kept for retry")
            sys.exit(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not args.full and not args.keep_delta and os.path.isdir(DELTA_DIR):
        shutil.rmtree(DELTA_DIR, ignore_errors=True)
        eprint("Delta files removed")

    counts = ",".join("%s=%d" % (k, len(v)) for k, v in sections.items())
    eprint("Done at %s [%s]" % (datetime.now(timezone.utc).isoformat(), counts))


if __name__ == "__main__":
    main()
