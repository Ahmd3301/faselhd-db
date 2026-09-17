"""Send dual-source update report (FaselHD + TopCinma) to Telegram bot."""
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "update_log.txt")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SOURCES = [
    {"tag": "faselhd", "label": "FaselHD", "icon": "🎬"},
    {"tag": "topcinma", "label": "TopCinma", "icon": "🎞"},
]

MONTHS_EN = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}


def format_date(iso_str):
    """Convert '2026-06-05T02:15:30Z' to 'Friday, June 5, 2026, 02:15:30 PM'."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        weekday = ["Monday", "Tuesday", "Wednesday", "Thursday",
                   "Friday", "Saturday", "Sunday"][dt.weekday()]
        month = MONTHS_EN.get(dt.strftime("%m"), dt.strftime("%m"))
        ampm = dt.strftime("%p")
        return f"{weekday}, {month} {dt.day}, {dt.year}, {dt.strftime('%I:%M:%S')} {ampm}"
    except Exception:
        return iso_str


def escape_html(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def parse_blocks():
    """Return {tag: latest block dict} for faselhd/topcinma (new + legacy formats)."""
    if not os.path.exists(LOG_PATH):
        return {}
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    entries = [e for e in content.strip().split("\n\n") if e.strip()]
    found = {}
    for entry in reversed(entries):
        lines = entry.strip().split("\n")
        tag = None
        m = re.match(r"Run:\s*(\S+)(?:\s*\[(\w+)\])?", lines[0].strip())
        if not m:
            continue
        run_time, tag = m.group(1), m.group(2)
        if tag not in ("faselhd", "topcinma"):
            tag = "faselhd"  # legacy untagged block
        if tag in found:
            continue
        info = {"run_time": run_time, "updated": [], "skipped": [],
                "total_new": 0, "changed": 0, "duration": "",
                "failed": None, "full": False}
        for line in lines[1:]:
            line = line.strip()
            if "FAILED:" in line:
                info["failed"] = line.split("FAILED:", 1)[1].strip()[:160]
            m2 = re.match(r"\[(.+?)\]\s*\+(\d+)\s*->\s*(.+)", line)
            if m2:
                sec, count = m2.group(1).strip(), int(m2.group(2))
                if count > 0:
                    info["updated"].append((sec, count))
                else:
                    info["skipped"].append(sec)
            m3 = re.match(r"Total new(?: \(\w+\))?: (\d+) items across (\d+) sections", line)
            if m3:
                info["total_new"], info["changed"] = int(m3.group(1)), int(m3.group(2))
            m4 = re.match(r"Duration(?: \(\w+\))?: (\S+)", line)
            if m4:
                info["duration"] = m4.group(1)
            if "full scrape" in line:
                info["full"] = True
        found[tag] = info
        if len(found) == 2:
            break
    return found


def get_run_number():
    env_num = os.environ.get("GITHUB_RUN_NUMBER")
    if env_num:
        return int(env_num)
    if not os.path.exists(LOG_PATH):
        return 0
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        return f.read().count("Run:")


def render_source(label, icon, info):
    lines = []
    if info is None:
        return [f"{icon} <b>{label}</b> — ⏸ no data yet"]
    if info["failed"]:
        return [f"{icon} <b>{label}</b> — 🔴 failed",
                f"<i>{escape_html(info['failed'])}</i>"]
    head = f"{icon} <b>{label}</b> — "
    if info["total_new"] > 0:
        head += f"🆕 +{info['total_new']} new"
    else:
        head += "✅ up to date"
    if info["duration"]:
        head += f" · ⏱ {escape_html(info['duration'])}"
    lines.append(head)
    if info["updated"]:
        rows = "\n".join(f"{sec:<20s}+{n} new"
                         for sec, n in sorted(info["updated"], key=lambda x: -x[1]))
        lines.append(f"<code>{escape_html(rows)}</code>")
    if info["skipped"]:
        lines.append(f"<i>skipped: {escape_html(', '.join(info['skipped']))}</i>")
    return lines


def build_message(run_num, blocks):
    fas = blocks.get("faselhd")
    top = blocks.get("topcinma")
    run_time = (fas or top or {}).get("run_time") or \
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"<b>🔄 Auto Update Report #{run_num}</b>",
             f"🗓 {format_date(run_time)}",
             "<b>━━━━━━━━━━━━━━</b>"]
    for src in SOURCES:
        lines.extend(render_source(src["label"], src["icon"], blocks.get(src["tag"])))
        lines.append("<b>━━━━━━━━━━━━━━</b>")
    total = sum((blocks.get(s["tag"]) or {}).get("total_new", 0) for s in SOURCES)
    failed = [s["label"] for s in SOURCES
              if (blocks.get(s["tag"]) or {}).get("failed")]
    f_n = (fas or {}).get("total_new", 0)
    t_n = (top or {}).get("total_new", 0)
    durs = [f"⏱ {d}" for d in ((fas or {}).get("duration"), (top or {}).get("duration")) if d]
    tail = f"📦 <b>Total: +{total} new</b> · FaselHD {f_n} + TopCinma {t_n}"
    if durs:
        tail += f" · {' + '.join(durs)}"
    if failed:
        tail += f" · ⚠ partial ({', '.join(failed)} failed)"
    lines.append(tail)
    lines.append("⏳ Next report in 30 minutes.")
    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({"chat_id": int(CHAT_ID), "text": message,
                       "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        print(f"Telegram error: {e}")
        return None


def main():
    run_num = get_run_number()
    blocks = parse_blocks()
    if not blocks:
        msg = (
            f"<b>🔄 Auto Update Report #{run_num}</b>\n"
            f"🗓 {format_date(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))}\n"
            f"<b>━━━━━━━━━━━━━━</b>\n"
            f"No update data available\n"
            f"<b>━━━━━━━━━━━━━━</b>\n"
            f"⏳ Next report in 30 minutes."
        )
    else:
        msg = build_message(run_num, blocks)
    result = send_telegram(msg)
    if result and result.get("ok"):
        print("Telegram notification sent OK")
    else:
        print(f"Telegram result: {result}")


if __name__ == "__main__":
    main()
