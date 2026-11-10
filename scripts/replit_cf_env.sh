#!/usr/bin/env bash
# تهيئة بيئة Replit لتشغيل متصفح patchright (تخطّي Cloudflare).
#
# المشكلة: chromium المُنزّل من patchright ليس مربوطاً بـ nix store، فلا يجد
# مكتبات النظام (libnspr4.so / libnss3.so ...) إلا إذا أضفنا مساراتها إلى
# LD_LIBRARY_PATH يدوياً.
#
# الحل: نجمع المسارات الناقصة فقط من /nix/store ونضيفها إلى LD_LIBRARY_PATH.
#
# الاستخدام:  source scripts/replit_cf_env.sh
#
# المتطلبات في .replit (جذر workspace):
#   [nix]
#   channel = "stable-25_05"
#   packages = [..., "chromium"]
# ثم Restart للـ workspace.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

say() { printf '[env] %s\n' "$*"; }

# ── 1) تحديد مسار متصفح patchright ────────────────────────────────────────
find_chrome() {
  local p
  for base in "$HOME/.cache/ms-playwright" "/home/runner/workspace/.cache/ms-playwright" \
              "/home/runner/.cache/ms-playwright" "./.cache/ms-playwright"; do
    [ -d "$base" ] || continue
    p="$(ls -d "$base"/chromium-*/chrome-linux64/chrome 2>/dev/null | head -1)"
    [ -n "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}

CHROME="$(find_chrome || true)"
if [ -z "$CHROME" ]; then
  say "لم أجد متصفح patchright — نفّذ: python3 -m patchright install chromium"
else
  say "chrome=$CHROME"
fi

# ── 2) تجميع المكتبات الناقصة من nix store ────────────────────────────────
if [ -n "$CHROME" ] && [ -d /nix/store ]; then
  missing="$(ldd "$CHROME" 2>/dev/null | awk '/not found/{print $1}' | sort -u)"
  if [ -z "$missing" ]; then
    say "كل المكتبات موجودة (no 'not found')"
  else
    for lib in $missing; do
      base="$(basename "$lib")"
      dir="$(find /nix/store -maxdepth 3 -name "$base" -printf '%h\n' 2>/dev/null | head -1)"
      if [ -n "$dir" ]; then
        case ":${LD_LIBRARY_PATH:-}:" in
          *":$dir:"*) : ;;
          *) export LD_LIBRARY_PATH="$dir:${LD_LIBRARY_PATH:-}";;
        esac
        say "$base -> $dir"
      else
        say "غير موجود في nix store: $base  (أضف الحزمة المناسبة إلى .replit ثم Restart)"
      fi
    done
    say "LD_LIBRARY_PATH تم تحديثه"
  fi
fi

# ── 3) متغيرات التخطي ─────────────────────────────────────────────────────
export CF_TARGET="${CF_TARGET:-https://www.fasel-hd.co/}"
export FASELHD_CF_HEADLESS="${FASELHD_CF_HEADLESS:-1}"
export FASELHD_CF_TIMEOUT="${FASELHD_CF_TIMEOUT:-120}"
export FASELHD_CF_DIAG_DIR="${FASELHD_CF_DIAG_DIR:-diag}"
export FASELHD_CF="${FASELHD_CF:-1}"
export FASELHD_BROWSER_CHANNEL="${FASELHD_BROWSER_CHANNEL:-chromium}"
# مستضيفات Turnstile attestation منشورة بـ IPv6 فقط → نوجّهها لـ IPv4
export FASELHD_HOST_RULES="${FASELHD_HOST_RULES:-MAP brunhild.challenges.cloudflare.com 104.18.17.146, MAP hagen.challenges.cloudflare.com 104.18.8.208}"

say "CF_TARGET=$CF_TARGET"
say "FASELHD_CF_HEADLESS=$FASELHD_CF_HEADLESS  timeout=$FASELHD_CF_TIMEOUT"
say "FASELHD_PROXY=${FASELHD_PROXY:-<none>}"
say "جاهز — نفّذ: python3 scripts/verify_cf_bypass.py"
