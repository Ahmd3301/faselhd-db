#!/usr/bin/env bash
# تشغيل اختبار تخطّي Cloudflare داخل Replit.
# ★ بدون بروكسي — اتصال مباشر فقط.
#
# Strategy:
#   1) headed تحت xvfb إن توفّر (يتخطّى Cloudflare أفضل من headless)
#   2) وإلا headless مع WebGL برمجي (swiftshader)
#   3) يجرّب النطاقين: .co ثم .cam حتى ينجح أحدهما
#
# الاستخدام:
#   source scripts/replit_cf_env.sh   # مرة واحدة لكل جلسة shell
#   bash scripts/replit_cf_run.sh

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

say() { printf '\n[run] %s\n' "$*"; }

# ── 1) منع أي بروكسي بشكل قاطع ────────────────────────────────────────────
unset FASELHD_PROXY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy \
      ALL_PROXY all_proxy 2>/dev/null || true
export no_proxy="*"
export NO_PROXY="*"
say "الاتصال: مباشر بدون بروكسي ✅"

# ── 2) الأهداف (الأولوية للـ .co لأنه ما ينجح على جهازك) ─────────────────
TARGETS="${FASELHD_TARGETS:-https://www.fasel-hd.co/ https://www.fasel-hd.cam/}"

# ── 3) اختيار الوضع ──────────────────────────────────────────────────────
if command -v xvfb-run >/dev/null 2>&1; then
  export FASELHD_CF_HEADLESS=0
  RUNNER=(xvfb-run -a --server-args="-screen 0 1920x1080x24")
  say "الوضع: headed داخل xvfb"
else
  export FASELHD_CF_HEADLESS=1
  RUNNER=()
  say "الوضع: headless (xvfb-run غير متاح)"
fi

export FASELHD_CF_TIMEOUT="${FASELHD_CF_TIMEOUT:-180}"
export FASELHD_CF_DIAG_DIR="${FASELHD_CF_DIAG_DIR:-diag}"
export FASELHD_BROWSER_CHANNEL="${FASELHD_BROWSER_CHANNEL:-chromium}"
export FASELHD_HOST_RULES="${FASELHD_HOST_RULES:-MAP brunhild.challenges.cloudflare.com 104.18.17.146, MAP hagen.challenges.cloudflare.com 104.18.8.208}"
export FASELHD_EXTRA_ARGS="${FASELHD_EXTRA_ARGS:---lang=en-US --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --window-size=1920,1080}"

FAILED=""
for t in $TARGETS; do
  say "الهدف: $t"
  rm -rf "$FASELHD_CF_DIAG_DIR"
  mkdir -p "$FASELHD_CF_DIAG_DIR"
  export CF_TARGET="$t"

  "${RUNNER[@]}" python3 scripts/verify_cf_bypass.py
  code=$?

  say "exit=$code  ($t)"
  if [ "$code" -eq 0 ]; then
    say "🎉 نجح التخطّي على $t"
    say "الجلسة محفوظة في .cf_session.json"
    exit 0
  fi
  FAILED="$FAILED $t($code)"
done

say "❌ فشل التخطّي على:$FAILED"
say "التشخيص:"
[ -f "$FASELHD_CF_DIAG_DIR/diag.json" ] && head -c 1200 "$FASELHD_CF_DIAG_DIR/diag.json"
echo
exit 1
