#!/usr/bin/env bash
# تشغيل جسر البروكسي داخل Replit: SOCKS5 (مُصادَقة) → HTTP محلي للمتصفح.
#
# لماذا نحتاجه:
#   1) Chromium لا يدعم مصادقة SOCKS5 داخل --proxy-server، فنمرّر كل شيء
#      عبر جسر HTTP محلي على 127.0.0.1.
#   2) IP Replit (datacenter) يُرفض من Cloudflare عادةً، لذا نحتاج IP سكني.
#
# المتطلّب: Secret في Replit باسم FASELHD_SOCKS5 بصيغة
#            socks5://user:pass@host:port
#
# الاستخدام:
#   source scripts/replit_cf_proxy.sh
#   python3 scripts/verify_cf_bypass.py
#
# ملاحظة: كل الطلبات (المتصفح + urllib) يجب أن تمرّ من نفس البروكسي،
# لأن cf_clearance مربوط بالـ IP و الـ User-Agent معاً.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

say() { printf '[proxy] %s\n' "$*"; }

UPSTREAM="${FASELHD_SOCKS5:-}"
if [ -z "$UPSTREAM" ]; then
  say "FASELHD_SOCKS5 غير مضبوط."
  say "أضفه كـ Secret في Replit:  Tools → Secrets → FASELHD_SOCKS5 = socks5://user:pass@host:port"
  say "أو صدّره مؤقتاً: export FASELHD_SOCKS5='socks5://user:pass@host:port'"
  exit 1
fi

PORT="${FASELHD_PROXY_PORT:-8899}"
LISTEN="127.0.0.1:$PORT"

# إن كان الجسر يعمل مسبقاً فلا/start_jump
if curl -s --max-time 3 -x "http://$LISTEN" https://api.ipify.org >/dev/null 2>&1; then
  say "الجسر يعمل مسبقاً على $LISTEN"
else
  say "تشغيل الجسر: $LISTEN -> $UPSTREAM"
  nohup python3 scripts/proxy_bridge.py \
    --listen "$LISTEN" \
    --upstream "$UPSTREAM" \
    --map 'brunhild.challenges.cloudflare.com=104.18.17.146,hagen.challenges.cloudflare.com=104.18.8.208' \
    > proxy.log 2>&1 &
  sleep 3
  head -5 proxy.log 2>/dev/null
fi

# توجيه المتصفح و urllib/aiohttp عبر نفس البروكسي
export FASELHD_PROXY="http://$LISTEN"
export HTTP_PROXY="$FASELHD_PROXY"
export HTTPS_PROXY="$FASELHD_PROXY"
export http_proxy="$FASELHD_PROXY"
export https_proxy="$FASELHD_PROXY"
# urllib يقرأ lowercase؛ نضمن وجوده
export no_proxy=""

say "FASELHD_PROXY=$FASELHD_PROXY"

# فحص سريع: هل يمرّ المتصفح/curl عبر البروكسي؟
IP_PROXY="$(curl -s --max-time 15 -x "$FASELHD_PROXY" https://api.ipify.org || echo FAIL)"
IP_DIRECT="$(curl -s --max-time 15 https://api.ipify.org || echo FAIL)"
say "IP عبر البروكسي : $IP_PROXY"
say "IP مباشر        : $IP_DIRECT"
if [ "$IP_PROXY" = "FAIL" ]; then
  say "تحذير: البروكسي لا يرد — راجع proxy.log"
elif [ "$IP_PROXY" = "$IP_DIRECT" ]; then
  say "تحذير: الـ IP لم يتغيّر — البروكسي لا يمرّر حركة المرور فعلياً"
else
  say "البروكسي يعمل والـ IP مختلف ✅"
fi
