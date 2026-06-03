#!/usr/bin/env python3
"""
FaselHD Scraper — التشغيل الرئيسي

الاستخدام:
    python run.py                          # مسح جميع الأقسام
    python run.py --section tvshows        # قسم واحد فقط
    python run.py --list                   # عرض الأقسام المتاحة
"""
import argparse
import subprocess
import sys
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECTIONS = [
    ("movies", 582),
    ("series", 169),
    ("anime", 78),
    ("asian-series", 61),
    ("asian-movies", 55),
    ("hindi", 36),
    ("anime-movies", 17),
    ("tvshows", 7),
]


def list_sections():
    print("الأقسام المتاحة:")
    for name, pages in SECTIONS:
        print(f"  {name:15s} — {pages} صفحة")
    print()
    print("الاستخدام: python run.py --section <section_name>")


def run_section(section_name, debug=False):
    output_dir = os.path.join(BASE_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable, "-m", "scrapy", "crawl", "faselhd",
        "-a", f"section={section_name}",
    ]

    if debug:
        cmd.extend(["-s", "LOG_LEVEL=DEBUG"])

    print(f"\n{'='*60}")
    print(f">>> START: {section_name}")
    print(f"{'='*60}")

    start = time.time()
    result = subprocess.run(cmd, cwd=BASE_DIR, stdout=None, stderr=None)
    elapsed = time.time() - start

    if result.returncode == 0:
        print(f">>> DONE: {section_name} in {elapsed:.0f}s")
    else:
        print(f">>> FAILED: {section_name} after {elapsed:.0f}s")

    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="FaselHD Scraper — JSON لكل قسم")
    parser.add_argument("--section", type=str, default=None, help="مسح قسم واحد فقط")
    parser.add_argument("--list", action="store_true", help="عرض الأقسام المتاحة")
    parser.add_argument("--debug", action="store_true", help="إظهار مخرجات debug")
    args = parser.parse_args()

    if args.list:
        list_sections()
        return

    if args.section:
        run_section(args.section, debug=args.debug)
        return

    for name, _ in SECTIONS:
        rc = run_section(name, debug=args.debug)
        if rc != 0:
            print(f"توقف بسبب فشل {name}")
            sys.exit(rc)

    print(f"\n{'='*60}")
    print(">>> ALL SECTIONS COMPLETE!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
