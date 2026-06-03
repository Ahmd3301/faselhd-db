import scrapy
from urllib.parse import urlparse, urlunparse, unquote

from faselhd_scraper.items import FaselhdItem

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


class FaselhdUpdateSpider(scrapy.Spider):
    name = "faselhd_update"

    custom_settings = {
        "ITEM_PIPELINES": {
            "faselhd_scraper.pipelines.ValidationPipeline": 100,
            "faselhd_scraper.pipelines.DuplicatesPipeline": 200,
        }
    }

    def __init__(self, section=None, max_pages=10, base_url=None, page=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.section_filter = section
        self.max_pages = int(max_pages) if max_pages else 10
        self.base_url = (base_url or "https://www.fasel-hd.cam").rstrip("/")
        self.page = int(page) if page else None
        self.collected = []

    async def start(self):
        if self.section_filter:
            targets = [s for s in SECTIONS if s["key"] == self.section_filter]
        else:
            targets = SECTIONS

        page_nums = [self.page] if self.page else range(1, self.max_pages + 1)
        for sec in targets:
            key = sec["key"]
            for p in page_nums:
                url = f"{self.base_url}{sec['path']}/page/{p}"
                yield scrapy.Request(
                    url=url,
                    callback=self.parse,
                    cb_kwargs={"section_key": key, "page": p},
                )

    def parse(self, response, section_key, page):
        containers = response.css("#postList .postDiv")

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
            item = FaselhdItem(
                section_key=section_key,
                rank=0,
                slug=extract_slug(norm_link),
                name=name,
                img=img_url.strip(),
                link=norm_link,
            )
            self.collected.append(dict(item))
            yield item
