#!/usr/bin/env python3
"""Standalone verification of the Cloudflare bypass used by faselhd.

The production implementation lives in ``cf_bypass.py``. This harness proves it
end-to-end and prints a JSON report:

  step 1: plain request without clearance -> must hit the challenge
  step 2: real browser solves the challenge -> cf_clearance obtained
  step 3: plain request WITH clearance + same UA -> real page + parsed items

Usage:
    py scripts/verify_cf_bypass.py                    (Windows, installed Chrome)
    xvfb-run -a python scripts/verify_cf_bypass.py    (Linux CI, bundled chromium)

Exit codes: 0 = PASS, 1 = browser/step-2 failure, 2 = challenge not solved,
            3 = clearance did not work for plain HTTP.

Env: see cf_bypass.py, plus CF_TARGET (base URL to test).
"""
import json
import os
import platform
import sys
import urllib.error  # noqa: F401  (kept for parity with cf_bypass)
import urllib.request  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cf_bypass  # noqa: E402

# Windows consoles default to cp1252 and crash on Arabic output; force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

TARGET = (os.environ.get("CF_TARGET") or "https://www.fasel-hd.co/").rstrip("/") + "/"

# Back-compat: earlier CI experiments used CF_* variable names.
for _old, _new in (
    ("CF_CHANNEL", "FASELHD_BROWSER_CHANNEL"),
    ("CF_HEADLESS", "FASELHD_CF_HEADLESS"),
    ("CF_SOLVE_TIMEOUT", "FASELHD_CF_TIMEOUT"),
    ("CF_DIAG_DIR", "FASELHD_CF_DIAG_DIR"),
):
    if os.environ.get(_old) and not os.environ.get(_new):
        os.environ[_new] = os.environ[_old]


def parse_items(html):
    try:
        import parsel
    except ImportError:  # pragma: no cover
        return None
    sel = parsel.Selector(text=html)
    items = []
    for c in sel.css("#postList .postDiv"):
        a = c.css("a")
        img = a.css("img") if a else None
        if not a or not img:
            continue
        name = img.attrib.get("alt", "").strip()
        src = img.attrib.get("data-src") or img.attrib.get("src", "")
        link = a.attrib.get("href", "")
        if name and src and link:
            items.append({"name": name, "img": src, "link": link})
    return items


def main():
    report = {
        "target": TARGET,
        "platform": platform.platform(),
        "channel": cf_bypass._channel(),
        "headless": cf_bypass._headless(),
        "proxy": bool(cf_bypass._proxy()),
    }

    # --- step 1: the challenge must be there for a plain client ---------------
    print("[1/3] Plain HTTP request without clearance ...", flush=True)
    r1 = cf_bypass.plain_fetch(TARGET)
    report["step1_plain"] = {
        "status": r1["status"],
        "challenged": cf_bypass.looks_like_challenge(r1["status"], r1["body"]),
        "error": r1.get("error"),
    }
    print(f"      status={r1['status']} challenged={report['step1_plain']['challenged']}",
          flush=True)

    # --- step 2: solve with a real browser ------------------------------------
    print(f"[2/3] Solving challenge with patchright ({report['channel']}) ...", flush=True)
    try:
        session = cf_bypass.solve(TARGET)
    except Exception as e:  # noqa: BLE001
        report["step2_solve"] = {"solved": False, "error": f"{type(e).__name__}: {e}"}
        d = {}
        diag_dir = cf_bypass._diag_dir()
        if diag_dir:
            try:
                with open(os.path.join(diag_dir, "diag.json"), "r", encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                d = {}
        print(f"      FAILED: {e}", flush=True)
        print(
            f"      diag: url={d.get('url')!r} cType={d.get('cType')!r} "
            f"turnstile_iframe={d.get('hasTurnstileIframe')} "
            f"turnstile_visible={d.get('turnstileVisible')}",
            flush=True,
        )
        print(f"      page_text={(d.get('bodyText') or '')[:300]!r}", flush=True)
        bad = [x for x in (d.get("netlog") or []) if x.get("failed") or x.get("status") != 200]
        if bad:
            print("      failing requests:", flush=True)
            for x in bad[:10]:
                print(f"        [{x.get('status')}] {x.get('failed') or ''} {x['url'][:120]}",
                      flush=True)
        report["ok"] = False
        print("\n=== REPORT ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    report["step2_solve"] = {
        "solved": True,
        "base_url": session["base_url"],
        "elapsed_s": session["elapsed_s"],
        "user_agent": session["user_agent"],
        "channel": session["channel"],
        "headless": session["headless"],
    }
    print(f"      solved=True in {session['elapsed_s']}s -> {session['base_url']}", flush=True)

    # --- step 3: reuse clearance for the real scrape request -------------------
    page1 = session["base_url"] + "movies/page/1"
    report["page1"] = page1
    print("[3/3] Plain HTTP request WITH clearance ...", flush=True)
    r3 = cf_bypass.plain_fetch(page1, cookie_header=session["cookie"],
                               ua=session["user_agent"])
    items = parse_items(r3["body"]) if r3["status"] == 200 else []
    report["step3_with_clearance"] = {
        "status": r3["status"],
        "final_url": r3.get("final_url"),
        "challenged": cf_bypass.looks_like_challenge(r3["status"], r3["body"]),
        "items_count": len(items) if items is not None else None,
        "sample": items[:3] if items else [],
        "error": r3.get("error"),
    }
    ok = bool(r3["status"] == 200 and not report["step3_with_clearance"]["challenged"]
              and items)
    report["ok"] = ok
    print(
        f"      status={r3['status']} challenged={report['step3_with_clearance']['challenged']} "
        f"items={report['step3_with_clearance']['items_count']}",
        flush=True,
    )
    print("\n=== REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌", flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
