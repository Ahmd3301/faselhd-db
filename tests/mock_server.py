"""Mock fasel-hd.cam server for testing Phase 2."""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HOST = "localhost"
PORT = 8765

SECTIONS = [
    "movies", "series", "anime", "asian-series",
    "asian-movies", "hindi", "anime-movies", "tvshows",
]
ITEMS_PER_PAGE = 24
MOCK_PAGES = 3
TOTAL_MOCK = MOCK_PAGES * ITEMS_PER_PAGE  # 72


def _init_store(section):
    _store[section] = []
    for i in range(1, TOTAL_MOCK + 1):
        _store[section].append({
            "name": f"Mock-{section}-{i}",
            "slug": f"mock-{section}-{i}",
            "img": f"https://static.faselhdcdn.com/mock/{section}/{i}.jpg",
            "link": f"https://www.fasel-hd.cam/seasons/mock-{section}-{i}",
        })


_store = {}
for sec in SECTIONS:
    _init_store(sec)


def _make_page_html(items):
    divs = ""
    for item in items:
        divs += (
            f'<div class="postDiv">'
            f'<a href="{item["link"]}">'
            f'<img alt="{item["name"]}" data-src="{item["img"]}" src="placeholder.jpg">'
            f'</a></div>'
        )
    return f'<!DOCTYPE html><html><body><div id="postList">{divs}</div></body></html>'


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = path.split("/")

        if len(parts) == 4 and parts[0] == "" and parts[2] == "page":
            section = parts[1]
            try:
                page = int(parts[3])
            except ValueError:
                self._send(404, b"Invalid page")
                return
            if section not in _store:
                self._send(404, b"Unknown section")
                return

            max_page = (len(_store[section]) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
            if page < 1 or page > max_page:
                self._send(404, b"Not found")
                return

            all_items = _store[section]
            start = (page - 1) * ITEMS_PER_PAGE
            page_items = all_items[start:start + ITEMS_PER_PAGE]
            html = _make_page_html(page_items)
            self._send(200, html.encode("utf-8"), content_type="text/html")
        else:
            self._send(404, b"Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path == "/admin/inject":
            section = qs.get("section", [None])[0]
            count_str = qs.get("count", ["1"])[0]
            try:
                count = int(count_str)
            except ValueError:
                self._send(400, b"Invalid count")
                return
            if section not in _store:
                self._send(404, b"Unknown section")
                return

            timestamp = int(time.time() * 1000)
            for i in range(count):
                slug = f"mock-{section}-NEW-{timestamp}-{i}"
                _store[section].insert(0, {
                    "name": f"Mock-{section}-NEW-{timestamp}-{i}",
                    "slug": slug,
                    "img": f"https://static.faselhdcdn.com/mock/{section}/NEW-{timestamp}-{i}.jpg",
                    "link": f"https://www.fasel-hd.cam/seasons/{slug}",
                })

            body = json.dumps({"injected": count}).encode("utf-8")
            self._send(200, body)

        elif path == "/admin/reset":
            section = qs.get("section", [None])[0]
            if section and section in _store:
                _init_store(section)
            elif not section:
                for sec in SECTIONS:
                    _init_store(sec)
            body = json.dumps({"reset": True}).encode("utf-8")
            self._send(200, body)

        elif path == "/admin/status":
            status = {sec: len(_store[sec]) for sec in SECTIONS}
            body = json.dumps(status).encode("utf-8")
            self._send(200, body)

        else:
            self._send(404, b"Not found")

    def _send(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def run_mock_server():
    server = HTTPServer((HOST, PORT), MockHandler)
    print(f"Mock fasel-hd.cam on http://{HOST}:{PORT}")
    print(f"  {len(SECTIONS)} sections, {TOTAL_MOCK} items each")
    print(f"  POST /admin/inject, /admin/reset, /admin/status")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_mock_server()
