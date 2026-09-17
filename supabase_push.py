#!/usr/bin/env python3
"""Push FaselHD data to Supabase Postgres in single-request batches.

Replaces d1_push.py (Cloudflare D1 removed).
- FULL  (--full):        backfill from output/*.json, ord = 1..N per section
- DELTA (default):       new items from output/.delta/*.json,
                         ord reserved below current MIN(ord) so newest stays first
                         under ORDER BY ord ASC (same contract as D1).
- Each batch of up to BATCH_SIZE rows = ONE HTTP request
  (POST /rest/v1/items?on_conflict=section_key,slug, ignore_duplicates=True
   == INSERT OR IGNORE).

Env (GitHub Secrets in CI):
  SUPABASE_URL                https://<ref>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY   sb_secret_... (or legacy service_role JWT)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DELTA_DIR = os.path.join(OUTPUT_DIR, ".delta")

BATCH_SIZE = 1000


def eprint(*args, **kwargs):
    print(*args, flush=True, **kwargs)


def get_client():
    try:
        from supabase import create_client
    except ImportError:
        eprint("ERROR: supabase package not installed (pip install -r requirements.txt)")
        sys.exit(1)
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or
           os.environ.get("SUPABASE_SECRET_KEY", "")).strip()
    if not url or not key:
        eprint("ERROR: missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY env vars")
        sys.exit(1)
    return create_client(url, key)


def load_section(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def collect_items(full_mode):
    sections = {}
    src_dir = OUTPUT_DIR if full_mode else DELTA_DIR
    if not full_mode and not os.path.isdir(src_dir):
        return None
    for fname in sorted(os.listdir(src_dir)):
        if not fname.endswith(".json"):
            continue
        data = load_section(os.path.join(src_dir, fname))
        items = data.get("items") or []
        key = data.get("section") or fname[:-5]
        sections[key] = items
    return sections


def query_min_ord(client, section):
    try:
        res = (client.table("items").select("ord")
               .eq("section_key", section).order("ord").limit(1).execute())
    except Exception as e:
        eprint(f"ERROR: cannot read MIN(ord) for {section}: {e}")
        eprint("HINT: run supabase_schema.sql in Supabase Dashboard > SQL Editor first.")
        sys.exit(1)
    rows = res.data or []
    return int(rows[0]["ord"]) if rows else 0


def to_row(section, item, ord):
    row = {
        "section_key": section,
        "slug": item.get("slug"),
        "name": item.get("name"),
        "img": item.get("img"),
        "link": item.get("link"),
        "ord": ord,
    }
    if item.get("added_at"):
        row["added_at"] = item.get("added_at")
    return row


def build_rows(sections, full_mode, client):
    all_rows = {}
    for section, items in sections.items():
        valid = [it for it in items if it.get("slug")]
        total = len(valid)
        if full_mode:
            next_ord = 1
        else:
            current_min = query_min_ord(client, section)
            # Delta items are newest-first; the newest must get the smallest
            # ord so it appears first under ORDER BY ord ASC.
            next_ord = current_min - total
        rows = []
        for item in valid:
            rows.append(to_row(section, item, next_ord))
            next_ord += 1
        all_rows[section] = rows
    return all_rows


def push_rows(client, all_rows):
    for section, rows in all_rows.items():
        if not rows:
            continue
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            try:
                client.table("items").upsert(
                    chunk,
                    on_conflict="section_key,slug",
                    ignore_duplicates=True,
                ).execute()
            except Exception as e:
                eprint(f"ERROR upsert {section} rows {i}-{i + len(chunk)}: {e}")
                eprint("FAILED: delta files kept for retry")
                sys.exit(1)
            eprint("  pushed %s [%d/%d] (1 request)" % (section, i + len(chunk), len(rows)))


def verify_counts(client, all_rows):
    for section, rows in all_rows.items():
        if not rows:
            continue
        try:
            res = (client.table("items").select("id", count="exact")
                   .eq("section_key", section).limit(0).execute())
            eprint("  verify %-15s count=%s" % (section, res.count))
        except Exception as e:
            eprint(f"  WARN verify {section}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Push FaselHD data to Supabase")
    parser.add_argument("--full", action="store_true",
                        help="Full backfill from output/*.json instead of .delta files")
    parser.add_argument("--keep-delta", action="store_true",
                        help="Do not delete delta files after success")
    args = parser.parse_args()

    sections = collect_items(args.full)
    if sections is None or not any(sections.values()):
        eprint("No data to push%s, exiting" % (" (--full)" if args.full else ": no .delta files"))
        return

    total = sum(len(v) for v in sections.values())
    eprint("Mode: %s | sections=%d | items=%d" % (
        "FULL" if args.full else "DELTA", len(sections), total))

    client = get_client()
    all_rows = build_rows(sections, args.full, client)
    if not any(all_rows.values()):
        eprint("Nothing to push after filtering, exiting")
        return
    push_rows(client, all_rows)
    verify_counts(client, all_rows)

    if not args.full and not args.keep_delta and os.path.isdir(DELTA_DIR):
        import shutil
        shutil.rmtree(DELTA_DIR, ignore_errors=True)
        eprint("Delta files removed")

    counts = ",".join("%s=%d" % (k, len(v)) for k, v in all_rows.items())
    eprint("Done at %s [%s]" % (datetime.now(timezone.utc).isoformat(), counts))


if __name__ == "__main__":
    main()
