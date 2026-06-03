import scrapy
from faselhd_scraper.items import FaselhdItem
from urllib.parse import urlparse, urlunparse, unquote

BASE_URL = "https://www.fasel-hd.cam"

SECTIONS = [
    {"key": "movies",        "path": "/movies",        "pages": 582},
    {"key": "series",        "path": "/series",        "pages": 169},
    {"key": "anime",         "path": "/anime",         "pages": 78},
    {"key": "asian-series",  "path": "/asian-series",  "pages": 61},
    {"key": "asian-movies",  "path": "/asian-movies",  "pages": 55},
    {"key": "hindi",         "path": "/hindi",         "pages": 36},
    {"key": "anime-movies",  "path": "/anime-movies",  "pages": 17},
    {"key": "tvshows",       "path": "/tvshows",       "pages": 7},
]


def normalize_link(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc="www.fasel-hd.cam"))


def extract_slug(url: str) -> str:
    path = urlparse(url).path
    decoded = unquote(path)
    return decoded.rstrip("/").split("/")[-1]


class FaselhdSpider(scrapy.Spider):
    name = "faselhd"
    allowed_domains = [
        "fasel-hd.cam", "www.fasel-hd.cam",
        "faselhdx.bid", "*.faselhdx.bid",
        "faselhdx.cam", "*.faselhdx.cam",
    ]

    def __init__(self, section=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.section_filter = section
        self._rank_counters = {}

    async def start(self):
        targets = SECTIONS
        if self.section_filter:
            targets = [s for s in SECTIONS if s["key"] == self.section_filter]
            if not targets:
                self.logger.error(f"Unknown section: {self.section_filter}")
                return

        for sec in targets:
            key = sec["key"]
            total_pages = sec["pages"]
            self._rank_counters[key] = 0
            for page in range(1, total_pages + 1):
                url = f"{BASE_URL}{sec['path']}/page/{page}"
                yield scrapy.Request(
                    url=url,
                    callback=self.parse,
                    cb_kwargs={"section_key": key, "page": page},
                )

    def parse(self, response, section_key, page):
        containers = response.css("#postList .postDiv")
        self.logger.info(f"[{section_key}] Page {page} — {len(containers)} items")

        for container in containers:
            anchor = container.css("a")
            if not anchor:
                continue
            img = anchor.css("img")
            if not img:
                continue

            name = img.attrib.get("alt", "").strip()
            img_url = img.attrib.get("data-src") or img.attrib.get("src", "")
            link = anchor.attrib.get("href", "")

            if not name or not img_url or not link:
                continue

            norm_link = normalize_link(link.strip())
            self._rank_counters[section_key] += 1

            yield FaselhdItem(
                section_key=section_key,
                rank=self._rank_counters[section_key],
                slug=extract_slug(norm_link),
                name=name,
                img=img_url.strip(),
                link=norm_link,
            )
