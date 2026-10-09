#!/usr/bin/env python3
"""Minimal Cloudflare Pages Direct-Upload client (mirrors wrangler).

Content-addressed uploads: a file is identified by
    hash = blake3(base64(content) + extension).hexdigest()[:32]
Only assets missing from the store are uploaded, so a run uploads just the
new posters. A deployment is then created from a full path -> hash manifest.

Env (GitHub Secrets in CI):
  CLOUDFLARE_API_TOKEN    Pages:Edit token
  CLOUDFLARE_ACCOUNT_ID   account id owning the project
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import blake3

API = "https://api.cloudflare.com/client/v4"

BULK_UPLOAD_CONCURRENCY = 4
MAX_BUCKET_SIZE = 50 * 1024 * 1024
MAX_BUCKET_FILE_COUNT = 1000

EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
    "image/svg+xml": "svg",
}


def eprint(*a, **k):
    print(*a, flush=True, **k)


def _token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not t:
        raise RuntimeError("missing CLOUDFLARE_API_TOKEN env var")
    return t


def _account():
    a = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not a:
        raise RuntimeError("missing CLOUDFLARE_ACCOUNT_ID env var")
    return a


def hash_content(data, ext):
    """Return the Pages asset hash for raw bytes + extension (no leading dot)."""
    ext = (ext or "").lstrip(".").lower()
    return blake3.blake3(base64.b64encode(data) + ext.encode("utf-8")).hexdigest()[:32]


def _request(method, url, token=None, data=None, headers=None, timeout=60,
             binary=False, retries=4):
    h = {}
    if headers:
        h.update(headers)
    if token:
        h["Authorization"] = f"Bearer {token}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            if binary:
                return body
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last = f"HTTP {e.code}: {body[:500]}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last)
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last)
    raise RuntimeError(last)


def get_upload_token(project):
    url = f"{API}/accounts/{_account()}/pages/projects/{project}/upload-token"
    res = _request("GET", url, token=_token())
    if not res.get("success"):
        raise RuntimeError(f"upload-token failed: {res.get('errors')}")
    return res["result"]["jwt"]


def check_missing(jwt, hashes):
    """Return the subset of hashes not present in the asset store."""
    url = f"{API}/pages/assets/check-missing"
    body = json.dumps({"hashes": list(hashes)}).encode("utf-8")
    res = _request("POST", url, token=jwt, data=body,
                   headers={"Content-Type": "application/json"})
    if not res.get("success"):
        raise RuntimeError(f"check-missing failed: {res.get('errors')}")
    return res["result"]


def _size(f):
    if f.get("data") is not None:
        return len(f["data"])
    return os.path.getsize(f["file"])


def _read(f):
    if f.get("data") is not None:
        return f["data"]
    with open(f["file"], "rb") as fh:
        return fh.read()


def _bucket(files):
    buckets, current, size = [], [], 0
    for f in files:
        n = _size(f)
        if current and (size + n > MAX_BUCKET_SIZE
                        or len(current) >= MAX_BUCKET_FILE_COUNT):
            buckets.append(current)
            current, size = [], 0
        current.append(f)
        size += n
    if current:
        buckets.append(current)
    return buckets


def upload_assets(jwt, files):
    """Upload only the given files. Each item is {hash,contentType,data|path}.

    Returns the number of files uploaded.
    """
    if not files:
        return 0
    buckets = _bucket(files)
    done = [0]

    def do_bucket(bucket):
        payload = [{
            "key": f["hash"],
            "value": base64.b64encode(_read(f)).decode("ascii"),
            "metadata": {"contentType": f["contentType"]},
            "base64": True,
        } for f in bucket]
        url = f"{API}/pages/assets/upload"
        res = _request("POST", url, token=jwt,
                       data=json.dumps(payload).encode("utf-8"),
                       headers={"Content-Type": "application/json"}, timeout=300)
        if not res.get("success"):
            raise RuntimeError(f"upload failed: {res.get('errors')}")
        done[0] += len(bucket)
        eprint(f"    uploaded {done[0]}/{len(files)} assets")

    with ThreadPoolExecutor(max_workers=BULK_UPLOAD_CONCURRENCY) as pool:
        futures = [pool.submit(do_bucket, b) for b in buckets]
        for fut in futures:
            fut.result()
    return done[0]


def upsert_hashes(jwt, hashes):
    url = f"{API}/pages/assets/upsert-hashes"
    body = json.dumps({"hashes": list(hashes)}).encode("utf-8")
    res = _request("POST", url, token=jwt, data=body,
                   headers={"Content-Type": "application/json"})
    if not res.get("success"):
        raise RuntimeError(f"upsert-hashes failed: {res.get('errors')}")


def create_deployment(project, manifest, branch="main", commit_message=None,
                      commit_hash=None):
    """Create a Pages deployment from a {path: hash} manifest."""
    boundary = "----cfpages" + uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    field("manifest", json.dumps(manifest, separators=(",", ":")))
    field("branch", branch)
    if commit_message:
        field("commit_message", commit_message[:384])
    if commit_hash:
        field("commit_hash", commit_hash)
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    url = f"{API}/accounts/{_account()}/pages/projects/{project}/deployments"
    res = _request("POST", url, token=_token(), data=body,
                   headers={"Content-Type":
                            f"multipart/form-data; boundary={boundary}"},
                   timeout=300)
    if not res.get("success"):
        raise RuntimeError(f"create deployment failed: {res.get('errors')}")
    return res["result"]


def deploy(project, manifest, new_assets, branch="main"):
    """Upload only new assets and deploy. Returns (deployment, uploaded_count).

    manifest: {"/path": hash} for the FULL site (old + new).
    new_assets: list of {hash,data,contentType} to (maybe) upload.
    """
    jwt = get_upload_token(project)
    new_hashes = [a["hash"] for a in new_assets]
    missing = set(check_missing(jwt, new_hashes)) if new_hashes else set()
    to_upload = [a for a in new_assets if a["hash"] in missing]
    uploaded = upload_assets(jwt, to_upload)
    if new_hashes:
        upsert_hashes(jwt, new_hashes)
    dep = create_deployment(project, manifest, branch=branch)
    return dep, uploaded


def upload_only(project, assets):
    """Upload only the assets missing from the store (no deployment).

    assets: list of {hash,contentType,data|path}. Returns uploaded count.
    """
    if not assets:
        return 0
    jwt = get_upload_token(project)
    hashes = [a["hash"] for a in assets]
    missing = set(check_missing(jwt, hashes))
    to_upload = [a for a in assets if a["hash"] in missing]
    uploaded = upload_assets(jwt, to_upload)
    upsert_hashes(jwt, hashes)
    return uploaded


def deploy_only(project, manifest, branch="main", commit_message=None,
                commit_hash=None):
    return create_deployment(project, manifest, branch=branch,
                             commit_message=commit_message, commit_hash=commit_hash)


def url_for(path):
    """Public URL for a manifest path (leading slash)."""
    base = os.environ.get("PAGES_BASE_URL", "https://imagecdn-agu.pages.dev").rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + urllib.parse.quote(path, safe="/")
