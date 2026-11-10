#!/usr/bin/env python3
"""Cloudflare "Just a moment..." bypass for faselhd.

Strategy (hybrid):
  1. A *real* browser (patchright — patched Playwright) opens the site once and
     lets Cloudflare's Managed Challenge complete normally.
  2. The resulting ``cf_clearance`` cookie is reused, together with the *exact*
     User-Agent that solved the challenge, for ordinary HTTP requests
     (``urllib`` and Scrapy). The browser is only needed once per IP.

Why the UA must match: Cloudflare binds ``cf_clearance`` to the UA (and the
client IP). Sending it with a different UA gets you a fresh challenge.

Environment variables
---------------------
FASELHD_CF            1|0          enable/disable the bypass (default 1)
FASELHD_CF_TIMEOUT    seconds      max time for the challenge (default 120)
FASELHD_CF_HEADLESS   1|0          run the browser headless (default 0; a headed
                                   browser passes far more often — on Linux CI
                                   run it under ``xvfb-run``)
FASELHD_BROWSER_CHANNEL  chrome|chromium   (default: chrome on Windows/CI-with-
                                   Chrome, chromium elsewhere)
FASELHD_PROXY         url          optional proxy for the browser; standard
                                   HTTPS_PROXY/HTTP_PROXY env vars are used by
                                   urllib/Scrapy automatically
FASELHD_CF_DIAG_DIR   path         dump screenshot + DOM + network log on failure

The clearance is cached in ``.cf_session.json`` (repo root, git-ignored) and
validated before every run, because it is IP-bound and expires.
"""

import json
import os
import platform
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, ".cf_session.json")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CHALLENGE_TITLE_RE = re.compile(
    r"just a moment|لحظة|attention required|un momento|проверка", re.I
)
CHALLENGE_BODY_MARKERS = (
    "_cf_chl_opt",
    "Just a moment",
    "التحقق من الأمن",
    "Performing security verification",
    "cf-turnstile",
    "cf-challenge",
)


def eprint(*a, **kw):
    print(*a, **kw, flush=True)


def enabled():
    return os.environ.get("FASELHD_CF", "1").strip() not in ("0", "false", "no")


def _timeout():
    return int(os.environ.get("FASELHD_CF_TIMEOUT", "120"))


def _headless():
    return os.environ.get("FASELHD_CF_HEADLESS", "0").strip() in ("1", "true", "yes")


def _channel():
    ch = os.environ.get("FASELHD_BROWSER_CHANNEL", "").strip()
    if ch:
        return ch
    return "chrome" if platform.system() == "Windows" else "chromium"


def _proxy():
    p = os.environ.get("FASELHD_PROXY", "").strip()
    if not p:
        p = os.environ.get("HTTPS_PROXY", "").strip() or os.environ.get("https_proxy", "").strip()
    if not p:
        return None
    if "://" not in p:
        p = "http://" + p
    return p


def _diag_dir():
    d = os.environ.get("FASELHD_CF_DIAG_DIR", "").strip()
    if not d:
        return None
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# plain HTTP
# --------------------------------------------------------------------------- #
def looks_like_challenge(status, body="", title=""):
    if status in (403, 503):
        return True
    if title and CHALLENGE_TITLE_RE.search(title):
        return True
    return any(m in (body or "") for m in CHALLENGE_BODY_MARKERS)


def plain_fetch(url, cookie_header=None, ua=None, timeout=45):
    """GET *url* with urllib; honours HTTPS_PROXY from the environment."""
    headers = {
        "User-Agent": ua or DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,ar-AR;q=0.9,en;q=0.8",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return {
            "status": resp.status,
            "final_url": resp.geturl(),
            "body": resp.read().decode("utf-8", "replace"),
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return {"status": e.code, "final_url": url, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "final_url": url, "body": "",
                "error": f"{type(e).__name__}: {e}"}


def build_cookie_header(cookies, host):
    host = host.lstrip(".")
    parts = []
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".")
        if not dom or host == dom or host.endswith("." + dom):
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def origin_of(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "/", "", "", ""))


# --------------------------------------------------------------------------- #
# browser solve
# --------------------------------------------------------------------------- #
def _dump_diag(page, solved, netlog, console):
    d = _diag_dir()
    info = {"solved": solved, "netlog": netlog, "console": console}
    if not d:
        return info
    try:
        page.screenshot(path=os.path.join(d, "challenge.png"), full_page=True)
    except Exception as e:  # noqa: BLE001
        info["screenshot_error"] = str(e)
    try:
        html = page.content()
        with open(os.path.join(d, "challenge.html"), "w", encoding="utf-8") as f:
            f.write(html)
        info["html_len"] = len(html)
    except Exception as e:  # noqa: BLE001
        info["html_error"] = str(e)
    try:
        info.update(page.evaluate("""() => {
            const f = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            const r = f ? f.getBoundingClientRect() : null;
            return {
                url: location.href,
                title: document.title,
                hasTurnstileIframe: !!f,
                turnstileVisible: !!(r && r.width > 0 && r.height > 0),
                hasCfOpt: typeof window._cf_chl_opt !== 'undefined',
                cType: (window._cf_chl_opt && window._cf_chl_opt.cType) || null,
                bodyText: (document.body ? document.body.innerText : '').slice(0, 400),
            };
        }"""))
    except Exception as e:  # noqa: BLE001
        info["eval_error"] = str(e)
    try:
        with open(os.path.join(d, "diag.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass
    return info


def _turnstile_box(page):
    try:
        return page.evaluate("""() => {
            const f = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (!f) return null;
            const r = f.getBoundingClientRect();
            if (!r || r.width <= 0 || r.height <= 0) return null;
            return {x: r.x, y: r.y, w: r.width, h: r.height};
        }""")
    except Exception:  # noqa: BLE001
        return None


def _click_turnstile(page):
    box = _turnstile_box(page)
    if not box:
        return False
    try:
        page.mouse.click(box["x"] + 30, box["y"] + box["h"] / 2)
        return True
    except Exception:  # noqa: BLE001
        return False


def solve(base_url, timeout=None, headless=None, channel=None):
    """Open *base_url* in a real browser and return the clearance session.

    Raises RuntimeError when the IP is hard-blocked or the challenge cannot be
    completed (e.g. no IPv6 and no reachable attestation host).
    """
    from patchright.sync_api import sync_playwright

    timeout = timeout or _timeout()
    headless = _headless() if headless is None else headless
    channel = channel or _channel()
    target = base_url if base_url.endswith("/") else base_url + "/"

    launch_opts = {"channel": channel, "headless": headless}
    extra_args = []
    if not headless and platform.system() == "Windows":
        # headed but off-screen: a real browser without a visible window
        extra_args += ["--window-position=-32000,-32000", "--window-size=1280,800"]
    # Cloudflare's Turnstile attestation host is IPv6-only; on machines without
    # IPv6 it fails with ERR_NAME_NOT_RESOLVED. Point it at a Cloudflare edge
    # that serves the same host over IPv4 (TLS SNI stays brunhild.*).
    rules = os.environ.get("FASELHD_HOST_RULES", "").strip()
    if rules:
        extra_args.append("--host-resolver-rules=" + rules)
    if extra_args:
        launch_opts["args"] = extra_args
    proxy = _proxy()
    if proxy:
        launch_opts["proxy"] = {"server": proxy}

    netlog, console = [], []

    def on_response(r):
        try:
            if len(netlog) < 40 and ("/cdn-cgi/" in r.url or r.status >= 400):
                netlog.append({"url": r.url[:300], "status": r.status})
        except Exception:  # noqa: BLE001
            pass

    def on_failed(r):
        try:
            if len(netlog) < 40:
                netlog.append({"url": r.url[:300], "failed": r.failure})
        except Exception:  # noqa: BLE001
            pass

    def on_console(m):
        try:
            if len(console) < 30:
                console.append(f"{m.type}: {m.text[:200]}")
        except Exception:  # noqa: BLE001
            pass

    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.on("response", on_response)
        page.on("requestfailed", on_failed)
        page.on("console", on_console)
        page.goto(target, wait_until="domcontentloaded", timeout=60000)

        title = final_title = ""
        clicks, last_click = 0, 0.0
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                title = page.title() or ""
                body = page.evaluate(
                    "document.body ? document.body.innerText.slice(0, 300) : ''"
                ) or ""
                if "Sorry, you have been blocked" in body or "Attention Required" in title:
                    raise RuntimeError(
                        "Cloudflare hard-blocked this IP: " + body[:160]
                    )
                still = page.evaluate(
                    "typeof window._cf_chl_opt !== 'undefined'"
                ) or bool(CHALLENGE_TITLE_RE.search(title))
            except RuntimeError:
                raise
            except Exception:
                # navigation in flight (301 .cam -> .co, challenge reload, ...)
                time.sleep(1.5)
                continue
            if not still:
                final_title = title
                break
            if time.time() - t0 > 6 and clicks < 3 and time.time() - last_click > 8:
                if _click_turnstile(page):
                    clicks += 1
                    last_click = time.time()
                    eprint("  [cf] clicked Turnstile checkbox")
                    time.sleep(2.5)
                    continue
            time.sleep(1.5)

        try:
            final_title = final_title or (page.title() or "")
            final_url = page.url or target
            ua = page.evaluate("navigator.userAgent")
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(1500)
            final_title = final_title or (page.title() or "")
            final_url = page.url or target
            ua = page.evaluate("navigator.userAgent")

        cookies = context.cookies()
        clearance = [c for c in cookies if c["name"] == "cf_clearance"]
        solved = bool(clearance) and not CHALLENGE_TITLE_RE.search(final_title)
        diag = _dump_diag(page, solved, netlog, console)
        page.close()
        context.close()
        browser.close()

    if not solved:
        ts = diag.get("hasTurnstileIframe")
        raise RuntimeError(
            "could not solve the Cloudflare challenge "
            f"(title={final_title!r}, turnstile={ts}, "
            f"elapsed={time.time() - t0:.0f}s). "
            "Common causes: datacenter/VPN IP needing attestation (Cloudflare "
            "'brunhild' is IPv6-only) or a hard IP block."
        )

    host = urlparse(final_url).hostname or urlparse(target).hostname
    return {
        "base_url": origin_of(final_url),
        "user_agent": ua,
        "cookie": build_cookie_header(cookies, host),
        "cookies": cookies,
        "channel": channel,
        "headless": headless,
        "elapsed_s": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------------- #
# cached session
# --------------------------------------------------------------------------- #
def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(session):
    payload = {k: v for k, v in session.items() if k != "cookies"}
    payload["cookie"] = session["cookie"]
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:  # pragma: no cover
        eprint(f"  [cf] could not cache clearance: {e}")


def clear_state():
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


def is_valid(session, probe_url=None):
    """A cached clearance is only useful if it still returns the real page."""
    if not session or not session.get("cookie") or not session.get("user_agent"):
        return False
    url = probe_url or session.get("base_url")
    if not url:
        return False
    r = plain_fetch(url, cookie_header=session["cookie"], ua=session["user_agent"])
    return r["status"] == 200 and not looks_like_challenge(r["status"], r["body"])


def get_session(base_url, force=False, probe_url=None):
    """Return a working clearance session for *base_url* (cached if possible).

    Returns ``None`` when the bypass is disabled; raises RuntimeError when it is
    enabled but cannot obtain clearance.
    """
    if not enabled():
        return None
    if not force:
        cached = load_state()
        if cached and is_valid(cached, probe_url):
            eprint(f"  [cf] reusing cached clearance for {cached.get('base_url')}")
            return cached
    eprint(f"  [cf] solving Cloudflare challenge for {base_url} ...")
    session = solve(base_url)
    save_state(session)
    eprint(
        f"  [cf] clearance OK in {session['elapsed_s']}s "
        f"(host={urlparse(session['base_url']).hostname}, "
        f"ua=...{session['user_agent'][-30:]})"
    )
    return session
