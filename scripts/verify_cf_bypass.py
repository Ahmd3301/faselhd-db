#!/usr/bin/env python3
"""Standalone verification of the Cloudflare bypass method used by faselhd-db.

Method (hybrid, works with plain HTTP clients):
  1. A real browser (patchright) solves the Cloudflare Managed Challenge once.
  2. The resulting `cf_clearance` cookie + the *identical* User-Agent are reused
     for ordinary HTTP requests (urllib), so the scraper no longer hits
     "Just a moment...".

This script proves the method end-to-end and prints a JSON report:
  - step 1: plain request without clearance  -> must hit the challenge
  - step 2: browser solves the challenge     -> cf_clearance obtained
  - step 3: plain request with clearance     -> real page + parsed items

Usage:
    py scripts/verify_cf_bypass.py            (Windows, uses installed Chrome)
    xvfb-run -a python scripts/verify_cf_bypass.py   (Linux CI, uses chromium)

Env:
    CF_TARGET    base URL        (default: https://www.fasel-hd.co/)
    CF_CHANNEL   chrome|chromium (default: chrome on Windows, chromium elsewhere)
    CF_HEADLESS  1|0             (default: 0 = headed; on CI run under xvfb-run)
"""
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request

# Windows consoles default to cp1252 and crash on Arabic output; force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

TARGET = os.environ.get("CF_TARGET", "https://www.fasel-hd.co/").rstrip("/") + "/"
PAGE1 = TARGET + "movies/page/1"
HEADLESS = os.environ.get("CF_HEADLESS", "0") == "1"
CHANNEL = os.environ.get("CF_CHANNEL") or ("chrome" if platform.system() == "Windows" else "chromium")
SOLVE_TIMEOUT = int(os.environ.get("CF_SOLVE_TIMEOUT", "90"))

CHALLENGE_TITLE_RE = re.compile(r"just a moment|لحظة|attention required|un momento", re.I)
CHALLENGE_BODY_MARKERS = (
    "_cf_chl_opt",
    "Just a moment",
    "التحقق من الأمان",
    "Performing security verification",
    "cf-turnstile",
)


def plain_fetch(url, cookie_header=None, ua=None):
    headers = {
        "User-Agent": ua or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,en;q=0.9",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=45)
        return {
            "status": resp.status,
            "final_url": resp.geturl(),
            "body": resp.read().decode("utf-8", "replace"),
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return {"status": e.code, "final_url": url, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "final_url": url, "body": "", "error": f"{type(e).__name__}: {e}"}


def looks_like_challenge(status, body, title=""):
    if status in (403, 503):
        return True
    if CHALLENGE_TITLE_RE.search(title or ""):
        return True
    return any(m in body for m in CHALLENGE_BODY_MARKERS)


def _diag_dir():
    d = os.environ.get("CF_DIAG_DIR")
    if not d:
        return None
    os.makedirs(d, exist_ok=True)
    return d


def _dump_diag(page, context, solved):
    """Save screenshot + DOM + challenge details so CI failures are debuggable."""
    d = _diag_dir()
    if not d:
        return {}
    info = {"solved": solved}
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
            const ts = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            const box = ts ? ts.getBoundingClientRect() : null;
            return {
                url: location.href,
                title: document.title,
                hasTurnstileIframe: !!ts,
                turnstileVisible: !!(box && box.width > 0 && box.height > 0),
                hasCfOpt: typeof window._cf_chl_opt !== 'undefined',
                cType: (window._cf_chl_opt && window._cf_chl_opt.cType) || null,
                bodyText: (document.body ? document.body.innerText : '').slice(0, 400),
            };
        }"""))
    except Exception as e:  # noqa: BLE001
        info["eval_error"] = str(e)
    try:
        import json as _json
        with open(os.path.join(d, "diag.json"), "w", encoding="utf-8") as f:
            _json.dump(info, f, ensure_ascii=False, indent=2)
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


def _try_click_turnstile(page):
    """Managed challenges with a visible checkbox need a real click."""
    box = _turnstile_box(page)
    if not box:
        return False
    try:
        # checkbox sits at the left side of the widget, vertically centred
        page.mouse.click(box["x"] + 30, box["y"] + box["h"] / 2)
        return True
    except Exception:  # noqa: BLE001
        return False


def solve_challenge():
    """Open the site in a real browser and wait for Cloudflare to issue clearance."""
    from patchright.sync_api import sync_playwright

    launch_opts = {"channel": CHANNEL, "headless": HEADLESS}
    if not HEADLESS and platform.system() == "Windows":
        # hidden headed window: fully off-screen, still a real browser
        launch_opts["args"] = ["--window-position=-32000,-32000", "--window-size=1280,800"]

    t0 = time.time()
    netlog = []
    console = []

    def _on_response(r):
        try:
            u = r.url
            if "/cdn-cgi/" in u or r.status >= 400:
                if len(netlog) < 40:
                    netlog.append({"url": u[:300], "status": r.status})
        except Exception:  # noqa: BLE001
            pass

    def _on_failed(r):
        try:
            if len(netlog) < 40:
                netlog.append({"url": r.url[:300], "failed": r.failure})
        except Exception:  # noqa: BLE001
            pass

    def _on_console(m):
        try:
            if len(console) < 30:
                console.append(f"{m.type}: {m.text[:200]}")
        except Exception:  # noqa: BLE001
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.on("response", _on_response)
        page.on("requestfailed", _on_failed)
        page.on("console", _on_console)
        resp = page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
        goto_headers = {}
        try:
            if resp is not None:
                goto_headers = {
                    k: v for k, v in (resp.all_headers() or {}).items()
                    if k.lower() in ("server", "cf-mitigated", "cf-ray", "cf-cache-status",
                                     "location", "content-type", "set-cookie")
                }
        except Exception:  # noqa: BLE001
            pass
        deadline = time.time() + SOLVE_TIMEOUT
        title = ""
        final_title = ""
        clicks = 0
        last_click = 0.0
        while time.time() < deadline:
            # Redirects (.cam -> .co) and challenge reloads destroy the JS context;
            # treat that as "still navigating" instead of a hard failure.
            try:
                title = page.title() or ""
                body = page.evaluate(
                    "document.body ? document.body.innerText.slice(0, 300) : ''"
                ) or ""
                if "Sorry, you have been blocked" in body or "Attention Required" in title:
                    page.close()
                    context.close()
                    browser.close()
                    raise RuntimeError(
                        "Cloudflare BLOCKED this IP (hard block, not a solvable challenge). "
                        f"title={title!r} body={body[:160]!r}"
                    )
                still_challenging = page.evaluate(
                    "typeof window._cf_chl_opt !== 'undefined'"
                ) or CHALLENGE_TITLE_RE.search(title)
            except RuntimeError:
                raise
            except Exception as e:  # navigation in flight, detached frame, ...
                banner = f"      (waiting: {type(e).__name__})"
                print(banner, flush=True)
                time.sleep(1.5)
                continue
            if not still_challenging:
                final_title = title
                break
            if time.time() - t0 > 6 and clicks < 3 and (time.time() - last_click) > 8:
                if _try_click_turnstile(page):
                    clicks += 1
                    last_click = time.time()
                    print("      (clicked Turnstile checkbox)", flush=True)
                    time.sleep(2.5)
                    continue
            time.sleep(1.5)

        try:
            final_title = final_title or (page.title() or "")
            final_url = page.url or TARGET
            ua = page.evaluate("navigator.userAgent")
        except Exception:  # pragma: no cover - last-resort settle
            page.wait_for_timeout(1500)
            final_title = final_title or (page.title() or "")
            final_url = page.url or TARGET
            ua = page.evaluate("navigator.userAgent")
        cookies = context.cookies()
        _solved = any(c["name"] == "cf_clearance" for c in cookies) and not \
            CHALLENGE_TITLE_RE.search(final_title)
        diag = _dump_diag(page, context, _solved)
        diag["netlog"] = netlog
        diag["console"] = console
        diag["goto_headers"] = goto_headers
        try:
            import json as _json
            if _diag_dir():
                with open(os.path.join(_diag_dir(), "diag.json"), "w", encoding="utf-8") as f:
                    _json.dump(diag, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass
        page.close()
        context.close()
        browser.close()

    elapsed = round(time.time() - t0, 1)
    clearance = [c for c in cookies if c["name"] == "cf_clearance"]
    return {
        "solved": _solved,
        "diag": diag,
        "title": final_title,
        "final_url": final_url,
        "user_agent": ua,
        "cookies": cookies,
        "cf_clearance": bool(clearance),
        "http_status": resp.status if resp else None,
        "elapsed_s": elapsed,
        "channel": CHANNEL,
        "headless": HEADLESS,
    }


def build_cookie_header(cookies, host=None):
    # send only cookies whose domain matches the host that actually served the page
    from urllib.parse import urlparse

    host = host or (urlparse(TARGET).hostname or "")
    parts = []
    for c in cookies:
        domain = (c.get("domain") or "").lstrip(".")
        if host == domain or host.endswith("." + domain):
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def parse_items(html):
    try:
        import parsel
    except ImportError:  # pragma: no cover - parsel is a repo dependency
        return None
    sel = parsel.Selector(text=html)
    items = []
    for container in sel.css("#postList .postDiv"):
        anchor = container.css("a")
        img = anchor.css("img") if anchor else None
        if not anchor or not img:
            continue
        name = img.attrib.get("alt", "").strip()
        img_url = img.attrib.get("data-src") or img.attrib.get("src", "")
        link = anchor.attrib.get("href", "")
        if name and img_url and link:
            items.append({"name": name, "img": img_url, "link": link})
    return items


def main():
    report = {"target": TARGET, "page1": PAGE1, "platform": platform.platform()}

    # --- step 1: prove the challenge exists for a plain client -----------------
    print("[1/3] Plain HTTP request without clearance ...", flush=True)
    r1 = plain_fetch(TARGET)
    report["step1_plain"] = {
        "status": r1["status"],
        "challenged": looks_like_challenge(r1["status"], r1["body"]),
        "error": r1.get("error"),
    }
    print(f"      status={r1['status']} challenged={report['step1_plain']['challenged']}", flush=True)

    # --- step 2: solve with a real browser ------------------------------------
    print("[2/3] Solving challenge with patchright (" + CHANNEL + ") ...", flush=True)
    try:
        solved = solve_challenge()
    except Exception as e:  # noqa: BLE001
        report["step2_solve"] = {"solved": False, "error": f"{type(e).__name__}: {e}"}
        print(f"      FAILED: {e}", flush=True)
        report["ok"] = False
        print("\n=== REPORT ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["step2_solve"] = {
        "solved": solved["solved"],
        "title": solved["title"],
        "cf_clearance": solved["cf_clearance"],
        "http_status": solved["http_status"],
        "elapsed_s": solved["elapsed_s"],
        "user_agent": solved["user_agent"],
        "channel": solved["channel"],
        "headless": solved["headless"],
        "diag": {k: v for k, v in (solved.get("diag") or {}).items()
                 if k not in ("bodyText",)},
        "diag_body_text": (solved.get("diag") or {}).get("bodyText"),
    }
    print(f"      solved={solved['solved']} title={solved['title']!r}", flush=True)
    if not solved["solved"]:
        report["ok"] = False
        d = solved.get("diag") or {}
        print(
            f"      diag: url={d.get('url')!r} cType={d.get('cType')!r} "
            f"turnstile_iframe={d.get('hasTurnstileIframe')} "
            f"turnstile_visible={d.get('turnstileVisible')}",
            flush=True,
        )
        print(f"      page_text={(d.get('bodyText') or '')[:300]!r}", flush=True)
        print("\n=== REPORT ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    # --- step 3: reuse clearance for the actual scrape request ----------------
    # The real site 301-redirects (www.fasel-hd.cam -> www.fasel-hd.co) and the
    # Cloudflare clearance cookie belongs to the *final* host, so we scrape that.
    from urllib.parse import urlparse, urlunparse

    _final = urlparse(solved.get("final_url") or TARGET)
    _base = urlunparse((_final.scheme, _final.netloc, "/", "", "", ""))
    page1 = _base + "movies/page/1"
    report["effective_base"] = _base
    report["page1"] = page1

    print("[3/3] Plain HTTP request WITH clearance ...", flush=True)
    cookie_header = build_cookie_header(solved["cookies"], host=_final.hostname)
    r3 = plain_fetch(page1, cookie_header=cookie_header, ua=solved["user_agent"])
    items = parse_items(r3["body"]) if r3["status"] == 200 else []
    report["step3_with_clearance"] = {
        "status": r3["status"],
        "final_url": r3.get("final_url"),
        "challenged": looks_like_challenge(r3["status"], r3["body"]),
        "items_count": len(items) if items is not None else None,
        "sample": items[:3] if items else [],
        "error": r3.get("error"),
    }
    ok = r3["status"] == 200 and not report["step3_with_clearance"]["challenged"] and (items or [])
    report["ok"] = bool(ok)
    print(
        f"      status={r3['status']} challenged={report['step3_with_clearance']['challenged']} "
        f"items={report['step3_with_clearance']['items_count']}",
        flush=True,
    )

    print("\n=== REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nRESULT:", "PASS ✅" if report["ok"] else "FAIL ❌", flush=True)
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
