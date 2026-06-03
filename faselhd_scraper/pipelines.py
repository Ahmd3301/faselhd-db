import json
import os
import logging
from datetime import datetime, timezone
from collections import defaultdict

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy import signals

from faselhd_scraper.items import FaselhdItem

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fasel-hd.cam"
OUTPUT_DIR = "output"


class ValidationPipeline:
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        name = adapter.get("name")
        img = adapter.get("img")
        link = adapter.get("link")
        if not name or not img or not link:
            raise DropItem(f"Missing fields: name={bool(name)}, img={bool(img)}, link={bool(link)}")
        return item


class DuplicatesPipeline:
    def __init__(self):
        self.seen = set()

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if adapter["link"] in self.seen:
            raise DropItem(f"Duplicate link: {adapter['link']}")
        self.seen.add(adapter["link"])
        return item


class JsonPerSectionPipeline:
    """تجميع العناصر حسب القسم وكتابة ملف JSON منظم عند الإغلاق"""

    def __init__(self):
        self.items_by_section = defaultdict(list)

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)
        return pipeline

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        section_key = adapter.get("section_key")
        if section_key:
            self.items_by_section[section_key].append(dict(adapter))
        return item

    def spider_closed(self, spider):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for section_key, items in self.items_by_section.items():
            output = {
                "section": section_key,
                "base_url": BASE_URL,
                "scraped_at": timestamp,
                "total": len(items),
                "items": [
                    {
                        "rank": it["rank"],
                        "slug": it["slug"],
                        "name": it["name"],
                        "img": it["img"],
                        "link": it["link"],
                        "added_at": timestamp,
                    }
                    for it in items
                ],
            }

            filepath = os.path.join(OUTPUT_DIR, f"{section_key}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            logger.info(f"Saved {len(items)} items → {filepath}")
