BOT_NAME = "faselhd_scraper"
SPIDER_MODULES = ["faselhd_scraper.spiders"]
NEWSPIDER_MODULE = "faselhd_scraper.spiders"

ROBOTSTXT_OBEY = False

# الموقع يعيد التوجيه إلى خوادم متطابقة (mirrors)، نلغي الـ OffsiteMiddleware لتجنب حظر الطلبات
DOWNLOADER_MIDDLEWARES = {
    "scrapy.downloadermiddlewares.offsite.OffsiteMiddleware": None,
}

DOWNLOAD_DELAY = 2.0
RANDOMIZE_DOWNLOAD_DELAY = True

CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1

RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408, 429]

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 5.0
AUTOTHROTTLE_MAX_DELAY = 60.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar-AR,ar;q=0.9,en;q=0.8",
    "Referer": "https://www.fasel-hd.cam/",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

ITEM_PIPELINES = {
    "faselhd_scraper.pipelines.ValidationPipeline": 100,
    "faselhd_scraper.pipelines.DuplicatesPipeline": 200,
    "faselhd_scraper.pipelines.JsonPerSectionPipeline": 300,
}

LOG_LEVEL = "INFO"
LOG_ENABLED = True
