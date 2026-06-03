#!/usr/bin/env python3
"""FaselHD Phase 2 — 8 tests using unittest + mock server."""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from urllib.request import urlopen, Request
from urllib.error import URLError
from http.client import HTTPException

# Pre-import mock_server so it's available in the daemon thread later
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
MOCK_URL = "http://localhost:8765"
REAL_SECTION = "tvshows"  # small section for dry-run test against real site
MOCK_SECTION = "series"  # section used for mock server tests


def _start_mock():
    import mock_server
    mock_server.run_mock_server()


def _output_path(section):
    return os.path.join(OUTPUT_DIR, f"{section}.json")


def _backup(section):
    path = _output_path(section)
    bak = path + ".bak"
    if os.path.exists(path):
        os.replace(path, bak)
        return True
    return False


def _restore(section):
    path = _output_path(section)
    bak = path + ".bak"
    if os.path.exists(bak):
        os.replace(bak, path)


def _delete(section):
    path = _output_path(section)
    if os.path.exists(path):
        os.remove(path)


def _scrape_mock_to_file(section, output_path):
    import tempfile

    tmp = tempfile.NamedTemporaryFile(
        suffix=".jl", delete=False, dir=BASE_DIR
    )
    tmp_path = tmp.name
    tmp.close()

    args = [
        sys.executable, "-m", "scrapy", "crawl", "faselhd_update",
        "-a", f"section={section}",
        "-a", f"max_pages=3",
        "-a", f"base_url={MOCK_URL}",
        "-o", tmp_path,
        "-s", "LOG_LEVEL=ERROR",
    ]
    result = subprocess.run(args, cwd=BASE_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Spider failed: {result.stderr}")

    items = []
    if os.path.exists(tmp_path):
        with open(tmp_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        os.unlink(tmp_path)

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data = {
        "section": section,
        "base_url": MOCK_URL,
        "scraped_at": timestamp,
        "total": len(items),
        "items": [
            {
                "rank": i + 1,
                "slug": it["slug"],
                "name": it["name"],
                "img": it["img"],
                "link": it["link"],
                "added_at": timestamp,
            }
            for i, it in enumerate(items)
        ],
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _run_update(section, extra_args=None):
    args = [
        sys.executable, "update.py",
        "--section", section,
        "--base-url", MOCK_URL,
    ]
    if extra_args:
        args.extend(extra_args)
    result = subprocess.run(args, cwd=BASE_DIR, capture_output=True, text=True)
    return result


def _admin_inject(section, count):
    url = f"{MOCK_URL}/admin/inject?section={section}&count={count}"
    req = Request(url, method="POST")
    resp = urlopen(req)
    return json.loads(resp.read())


def _admin_reset(section=None):
    qs = f"?section={section}" if section else ""
    url = f"{MOCK_URL}/admin/reset{qs}"
    req = Request(url, method="POST")
    urlopen(req)


class MockOutputManager:
    """Context manager to save/restore a section's output file around a test."""

    def __init__(self, section):
        self.section = section
        self.had_backup = False

    def __enter__(self):
        self.had_backup = _backup(self.section)
        return self

    def __exit__(self, *args):
        _delete(self.section)
        if self.had_backup:
            _restore(self.section)


class TestPhase2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_thread = threading.Thread(target=_start_mock, daemon=True)
        cls.mock_thread.start()
        time.sleep(0.3)

    # ----------------------------------------------------------------
    # TEST 1: Mock server reachable
    # ----------------------------------------------------------------
    def test_1_mock_reachable(self):
        for attempt in range(10):
            try:
                resp = urlopen(f"{MOCK_URL}/{MOCK_SECTION}/page/1", timeout=2)
                break
            except Exception:
                if attempt == 9:
                    raise
                time.sleep(1)
        self.assertEqual(resp.status, 200)
        html = resp.read().decode("utf-8")
        self.assertIn('<div id="postList">', html)
        self.assertEqual(html.count("postDiv"), 24)

    # ----------------------------------------------------------------
    # TEST 2: Mock server 404 on nonexistent page
    # ----------------------------------------------------------------
    def test_2_mock_404_last_page(self):
        try:
            urlopen(f"{MOCK_URL}/{MOCK_SECTION}/page/4")
            self.fail("Expected 404")
        except HTTPException as e:
            self.assertEqual(e.code, 404)
        except URLError as e:
            self.assertEqual(getattr(e, 'code', None), 404)

    # ----------------------------------------------------------------
    # TEST 3: Full scrape on mock server
    # ----------------------------------------------------------------
    def test_3_full_scrape_on_mock(self):
        _admin_reset(MOCK_SECTION)
        with MockOutputManager(MOCK_SECTION):
            data = _scrape_mock_to_file(MOCK_SECTION, _output_path(MOCK_SECTION))
            self.assertEqual(data["total"], 72)
            self.assertEqual(data["items"][0]["rank"], 1)
            self.assertEqual(data["items"][-1]["rank"], 72)
            slugs = [it["slug"] for it in data["items"]]
            self.assertEqual(len(slugs), len(set(slugs)), "slugs must be unique")

    # ----------------------------------------------------------------
    # TEST 4: Update detects injected items
    # ----------------------------------------------------------------
    def test_4_update_detects_injected_items(self):
        _admin_reset(MOCK_SECTION)
        with MockOutputManager(MOCK_SECTION):
            _scrape_mock_to_file(MOCK_SECTION, _output_path(MOCK_SECTION))
            _admin_inject(MOCK_SECTION, 5)

            result = _run_update(MOCK_SECTION)
            self.assertEqual(result.returncode, 0)

            with open(_output_path(MOCK_SECTION), "r") as f:
                data = json.load(f)
            self.assertEqual(data["total"], 77)
            for i in range(5):
                self.assertIn("NEW", data["items"][i]["slug"])
            self.assertIn("scraped_at", data)

    # ----------------------------------------------------------------
    # TEST 5: Update detects full page of new items
    # ----------------------------------------------------------------
    def test_5_update_detects_full_page(self):
        _admin_reset(MOCK_SECTION)
        with MockOutputManager(MOCK_SECTION):
            _scrape_mock_to_file(MOCK_SECTION, _output_path(MOCK_SECTION))
            _admin_inject(MOCK_SECTION, 24)

            result = _run_update(MOCK_SECTION)
            self.assertEqual(result.returncode, 0)

            with open(_output_path(MOCK_SECTION), "r") as f:
                data = json.load(f)
            self.assertEqual(data["total"], 96)

    # ----------------------------------------------------------------
    # TEST 6: Update skips unchanged section
    # ----------------------------------------------------------------
    def test_6_update_skips_unchanged(self):
        _admin_reset(MOCK_SECTION)
        with MockOutputManager(MOCK_SECTION):
            _scrape_mock_to_file(MOCK_SECTION, _output_path(MOCK_SECTION))
            with open(_output_path(MOCK_SECTION), "r") as f:
                before = json.load(f)

            result = _run_update(MOCK_SECTION)
            self.assertEqual(result.returncode, 0)

            with open(_output_path(MOCK_SECTION), "r") as f:
                after = json.load(f)
            self.assertEqual(before["total"], after["total"])
            self.assertEqual(before["scraped_at"], after["scraped_at"])
            self.assertIn("no_changes", result.stdout.lower())

    # ----------------------------------------------------------------
    # TEST 7: Dry-run against real site
    # ----------------------------------------------------------------
    def test_7_dry_run_real_site(self):
        # Use real site data — backup mock-free section
        had = _backup(REAL_SECTION)
        try:
            result = subprocess.run(
                [sys.executable, "update.py", "--section", REAL_SECTION, "--dry-run"],
                cwd=BASE_DIR, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(
                "no_changes" in result.stdout.lower() or
                "full_scrape_needed" in result.stdout.lower()
            )
        finally:
            _delete(REAL_SECTION)
            if had:
                _restore(REAL_SECTION)

    # ----------------------------------------------------------------
    # TEST 8: github_push.py dry-run
    # ----------------------------------------------------------------
    def test_8_github_push_dry_run(self):
        result = subprocess.run(
            [sys.executable, "github_push.py", "--dry-run", "--sections", "series"],
            cwd=BASE_DIR, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("[DRY-RUN]", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
