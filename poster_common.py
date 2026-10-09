#!/usr/bin/env python3
"""Shared helpers for hosting posters on Cloudflare Pages.

Convention:
  * only tc-* (TopCinma) and os-* (Ostora) posters are hosted
  * asset path is content-addressed: /<tc|os>/<blake3>.<ext>
  * a committed imagecdn/manifest.json maps every path -> hash (the
    full deployment manifest; makes incremental deploys possible)
  * item fields: poster_ws = original website poster, poster_cf = Pages URL
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from cf_pages import hash_content

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_CINMA_ROOT = os.path.join(BASE_DIR, "output", "TopCinma")
OSTORA_ROOT = os.path.join(BASE_DIR, "output", "Ostora")
POSTER_ROOTS = [TOP_CINMA_ROOT, OSTORA_ROOT]

IMAGE_CDN_DIR = os.path.join(BASE_DIR, "imagecdn")
MANIFEST_PATH = os.path.join(IMAGE_CDN_DIR, "manifest.json")

PAGES_PROJECT = "imagecdn"
PAGES_BASE = os.environ.get("PAGES_BASE_URL",
                            "https://imagecdn-agu.pages.dev").rstrip("/")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_EXT = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif", "image/avif": "avif",
    "image/svg+xml": "svg",
}


def eprint(*a, **k):
    print(*a, flush=True, **k)


def is_poster_section(section):
    return bool(section) and (section.startswith("tc-") or section.startswith("os-"))


def source_of(section):
    return "tc" if section.startswith("tc-") else "os"


def cf_url(path):
    if not path.startswith("/"):
        path = "/" + path
    return PAGES_BASE + urllib.parse.quote(path, safe="/")


def ext_from(content_type, url):
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _EXT:
        return _EXT[ct]
    path = urllib.parse.urlparse(url or "").path
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext in ("jpg", "jpeg"):
        return "jpg"
    if ext in ("png", "webp", "gif", "avif", "svg"):
        return ext
    return "jpg"


def http_get_bytes(url, timeout=30, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None, ""
        except Exception:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None, ""
    return None, ""


def build_asset(img_url, section):
    """Download a poster and return an in-memory upload descriptor, or None."""
    data, ctype = http_get_bytes(img_url)
    if not data:
        return None
    return _asset(data, ctype, img_url, section)


def stage_asset(img_url, section, stage_dir):
    """Download a poster to stage_dir and return an on-disk descriptor, or None."""
    data, ctype = http_get_bytes(img_url)
    if not data:
        return None
    ext = ext_from(ctype, img_url)
    h = hash_content(data, ext)
    remote = f"/{source_of(section)}/{h}.{ext}"
    os.makedirs(stage_dir, exist_ok=True)
    file_path = os.path.join(stage_dir, f"{h}.{ext}")
    with open(file_path, "wb") as f:
        f.write(data)
    return {
        "hash": h,
        "file": file_path,
        "contentType": ctype or "application/octet-stream",
        "ext": ext,
        "remote": remote,
        "cf": cf_url(remote),
        "ws": img_url,
    }


def _asset(data, ctype, img_url, section):
    ext = ext_from(ctype, img_url)
    h = hash_content(data, ext)
    remote = f"/{source_of(section)}/{h}.{ext}"
    return {
        "hash": h,
        "data": data,
        "contentType": ctype or "application/octet-stream",
        "ext": ext,
        "remote": remote,
        "cf": cf_url(remote),
        "ws": img_url,
    }


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return {}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest):
    os.makedirs(IMAGE_CDN_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=0, sort_keys=True)


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def git(*args):
    return subprocess.run(["git", *args], cwd=BASE_DIR,
                          capture_output=True, text=True)


def commit_and_push(paths, message):
    """Stage the given paths, commit and push if anything changed."""
    email = os.environ.get("GIT_USER_EMAIL")
    name = os.environ.get("GIT_USER_NAME")
    if email:
        git("config", "user.email", email)
    if name:
        git("config", "user.name", name)
    result = git("add", "--", *paths)
    if result.returncode != 0:
        raise RuntimeError(f"git add failed: {result.stderr}")
    if git("diff", "--cached", "--quiet").returncode == 0:
        eprint("Nothing staged -- skipping commit")
        return False
    result = git("commit", "-m", message)
    if result.returncode != 0:
        raise RuntimeError(f"git commit failed: {result.stderr}")
    result = git("push")
    if result.returncode != 0:
        raise RuntimeError(f"git push failed: {result.stderr}")
    eprint(f"Pushed: {message}")
    return True
