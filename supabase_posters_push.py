#!/usr/bin/env python3
"""Update poster_ws / poster_cf for existing rows (PostgREST ON CONFLICT DO
UPDATE). Used by the backfill workflow after posters are hosted on Pages.

Only these columns are sent, so ord/name/img/link are never touched.

Env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POSTER_ROOTS = [
    os.path.join(BASE_DIR, "output", "TopCinma"),
    os.path.join(BASE_DIR, "output", "Ostora"),
]
BATCH_SIZE = 1000


def eprint(*a, **k):
    print(*a, flush=True, **k)


def get_client():
    try:
        from supabase import create_client
    except ImportError:
        eprint("ERROR: supabase package not installed")
        sys.exit(1)
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or
           os.environ.get("SUPABASE_SECRET_KEY", "")).strip()
    if not url or not key:
        eprint("ERROR: missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
        sys.exit(1)
    return create_client(url, key)


def is_poster_section(section):
    return section.startswith("tc-") or section.startswith("os-")


def collect_rows():
    rows = []
    for root in POSTER_ROOTS:
        if not os.path.isdir(root):
            continue
        for fname in sorted(os.listdir(root)):
            if not fname.endswith(".json"):
                continue
            section = fname[:-5]
            if not is_poster_section(section):
                continue
            with open(os.path.join(root, fname), "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            for it in data.get("items", []):
                slug = it.get("slug")
                cf = it.get("poster_cf")
                if not slug or not cf:
                    continue
                rows.append({
                    "section_key": section,
                    "slug": slug,
                    "poster_ws": it.get("poster_ws") or it.get("img") or "",
                    "poster_cf": cf,
                })
    return rows


def main():
    rows = collect_rows()
    if not rows:
        eprint("No hosted posters to record")
        return
    eprint(f"Updating {len(rows)} rows with poster_ws/poster_cf")
    client = get_client()
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        try:
            client.table("items").upsert(
                chunk, on_conflict="section_key,slug").execute()
        except Exception as e:  # noqa: BLE001
            eprint(f"ERROR upsert rows {i}-{i + len(chunk)}: {e}")
            eprint("HINT: run supabase_posters_migration.sql first.")
            sys.exit(1)
        eprint(f"  pushed {i + len(chunk)}/{len(rows)}")
    eprint(f"Done at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
