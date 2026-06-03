# FaselHD Scraper — Full Project Analysis

## 1. File Tree

```
E:\faselhd/
├── sp.js                                    # Reference scraper (Node.js/axios/cheerio) — NOT used
│
└── faselhd-scraper/                         # Scrapy project root
    ├── scrapy.cfg                           # Scrapy project config
    ├── requirements.txt                     # Dependencies
    ├── pytest.ini                           # Test config (asyncio_mode=auto)
    ├── run.py                               # CLI entry point
    │
    ├── output/                              # Generated JSON files (8)
    │   ├── tvshows.json                     #   7 pages →   167 items
    │   ├── anime-movies.json                #  17 pages →   391 items
    │   ├── hindi.json                       #  36 pages →   855 items
    │   ├── asian-movies.json                #  55 pages → 1,309 items
    │   ├── asian-series.json                #  61 pages → 1,444 items
    │   ├── anime.json                       #  78 pages → 1,860 items
    │   ├── series.json                      # 169 pages → 4,036 items
    │   └── movies.json                      # 582 pages → 13,957 items
    │   ─────────────── Total                #          → 24,019 items
    │
    ├── faselhd_scraper/                     # Main package
    │   ├── __init__.py                      # Empty
    │   ├── items.py                         # FaselhdItem schema
    │   ├── middlewares.py                   # Rate-limit logger
    │   ├── pipelines.py                     # 3 processing pipelines
    │   ├── settings.py                      # Scrapy configuration
    │   │
    │   └── spiders/
    │       ├── __init__.py
    │       ├── faselhd_spider.py            # Main spider (async start, parse)
    │       └── test_spider.py               # 11 unit tests
    │
    └── tests/
        └── __init__.py
```

## 2. Scraper Flow

```
run.py
  │
  └── for each section (movies → tvshows):
       │
       ├── subprocess("scrapy crawl faselhd -a section=<key>")
       │
       └── faselhd_spider.py
            │
            ├── async def start()
            │   ├── 1 section × N pages → N scrapy.Request(url)
            │   └── per-section rank counter reset
            │
            ├── parse(response, section_key, page)
            │   ├── CSS: #postList .postDiv → a → img
            │   ├── Fields: name(img[alt]), img(data-src→src), link(a[href])
            │   ├── normalize_link() → force www.fasel-hd.cam
            │   ├── extract_slug() → last URL path segment decoded
            │   └── yield FaselhdItem(rank++, slug, name, img, link)
            │
            └── Pipelines (ordered):
                ├── [100] ValidationPipeline   — drop missing fields
                ├── [200] DuplicatesPipeline   — drop duplicate links
                └── [300] JsonPerSectionPipeline
                    ├── buffer items by section_key
                    └── on spider_closed → write output/<key>.json
```

## 3. JSON Schema (output files)

```json
{
  "section": "anime",
  "base_url": "https://www.fasel-hd.cam",
  "scraped_at": "2026-06-02T20:15:33Z",
  "total": 1860,
  "items": [
    {
      "rank": 1,
      "slug": "انمي-snowball-earth",
      "name": "انمي Snowball Earth",
      "img": "https://static.faselhdcdn.com/.../file.jpg?resize=400%2C600",
      "link": "https://www.fasel-hd.cam/anime/%d8%a7%d9%86%d9%85%d9%8a-snowball-earth",
      "added_at": "2026-06-02T20:15:33Z"
    }
  ]
}
```

No `db/` directory or `.checkpoint.json` exists anywhere in the project.

## 4. Anti-Bot Measures

| Measure | Implementation |
|---------|---------------|
| Realistic User-Agent | Chrome 131 / Win10 |
| Arabic Accept-Language | `ar-AR,ar;q=0.9,en;q=0.8` |
| Referer header | `https://www.fasel-hd.cam/` |
| Download delay | 2s base + 50% random jitter |
| AutoThrottle | start=5s, max=60s, target_concurrency=1.0 |
| Retry | 3× for 5xx, 4xx (429), 408 |
| Single concurrency | 1 request at a time |
| OffsiteMiddleware | Disabled (mirror domains redirect) |

## 5. Phase 2 Checklist (Auto-Update)

### Can reuse now:
- `normalize_link()`, `extract_slug()`, `FaselhdItem`
- All 3 pipelines (add a DB variant)
- CSS selector logic, settings, tests

### Must build:
- `db/` directory with SQLite schema
- `db/.checkpoint.json` for page-level resume
- Differential update logic (skip pages already scraped)
- Scheduling / cron wrapper
- Change detection (hash of name+img+link per slug)
