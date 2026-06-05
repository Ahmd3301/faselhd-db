"""Send update report to Telegram bot."""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "update_log.txt")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MONTHS_EN = {
    "01":"January","02":"February","03":"March","04":"April","05":"May","06":"June",
    "07":"July","08":"August","09":"September","10":"October","11":"November","12":"December",
}

def format_date(iso_str):
    """Convert '2026-06-05T02:15:30Z' to 'Friday, June 5, 2026, 02:15:30 PM'."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        weekday = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][dt.weekday()]
        month = MONTHS_EN.get(dt.strftime("%m"), dt.strftime("%m"))
        ampm = dt.strftime("%p")
        return f"{weekday}, {month} {dt.day}, {dt.year}, {dt.strftime('%I:%M:%S')} {ampm}"
    except:
        return iso_str

def parse_log():
    """Parse the latest run entry from update_log.txt."""
    if not os.path.exists(LOG_PATH):
        return None
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by "Run:" entries
    entries = content.strip().split("\n\n")
    if not entries:
        return None

    latest = entries[-1]
    lines = latest.strip().split("\n")

    run_time = ""
    sections = []
    total_new = 0
    changed_sections = 0
    duration = ""

    for line in lines:
        line = line.strip()
        if line.startswith("Run:"):
            run_time = line.replace("Run:", "").strip()
        elif line.startswith("Total new:"):
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "new":
                    total_new = parts[i-1]
                elif p == "across" and i + 2 < len(parts):
                    changed_sections = parts[i+1]
            sections.append(line)
        elif line.startswith("Duration:"):
            duration = line.replace("Duration:", "").strip()
        elif line.startswith("[") and "]" in line:
            sec_name = line.split("]")[0].lstrip("[").strip()
            status_part = line.split("]")[1].strip()
            sections.append(f"  {sec_name}: {status_part}")

    return {
        "run_time": run_time,
        "sections": sections,
        "total_new": total_new,
        "changed_sections": changed_sections,
        "duration": duration,
    }

def count_reports():
    """Count number of past reports in log."""
    if not os.path.exists(LOG_PATH):
        return 0
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    return content.count("Run:")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({"chat_id": int(CHAT_ID), "text": message, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        print(f"Telegram error: {e}")
        return None

def main():
    report_num = count_reports()
    info = parse_log()

    if not info:
        msg = (
            f"Report No. ({report_num})\n"
            f"{format_date(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))}\n"
            f"{'─'*30}\n"
            f"No update data available\n"
            f"{'─'*30}\n"
            f"The next report will be available in half an hour."
        )
        send_telegram(msg)
        return

    run_dt = info["run_time"] if info["run_time"] else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = format_date(run_dt)

    lines = [f"Report No. ({report_num})", date_str, "─"*30]

    has_changes = False
    for s in info["sections"]:
        if "no_changes" not in s and "skipped" not in s and "Duration" not in s and "Total" not in s:
            has_changes = True
        lines.append(s)

    if has_changes:
        lines.append(f"\nNew items: {info['total_new']} across {info.get('changed_sections', 0)} sections")
    else:
        lines.append("\nNo new items found - all sections up to date")

    if info["duration"]:
        lines.append(f"Duration: {info['duration']}")

    lines.append("─"*30)
    lines.append("The next report will be available in half an hour.")

    msg = "\n".join(lines)
    result = send_telegram(msg)
    if result and result.get("ok"):
        print("Telegram notification sent OK")
    else:
        print(f"Telegram result: {result}")

if __name__ == "__main__":
    main()
