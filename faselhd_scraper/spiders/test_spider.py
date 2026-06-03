"""
اختبار وحدة: يختبر استخراج البيانات من HTML وهمي
"""
import pytest
from scrapy.http import HtmlResponse
from faselhd_scraper.spiders.faselhd_spider import FaselhdSpider


def _make_response(body: str, url: str = "https://www.fasel-hd.cam/movies/page/1"):
    return HtmlResponse(url=url, body=body.encode("utf-8"), encoding="utf-8")


SAMPLE_HTML = """<!DOCTYPE html>
<html><body>
<div id="postList">
  <div class="postDiv">
    <a href="https://www.fasel-hd.cam/movie/test-film">
      <img alt="فيلم Test" data-src="https://img.example.com/test.jpg" src="placeholder.jpg">
    </a>
  </div>
  <div class="postDiv">
    <a href="https://www.fasel-hd.cam/movie/film2">
      <img alt="فيلم ثاني" src="https://img.example.com/film2.jpg">
    </a>
  </div>
  <div class="postDiv">
    <a href="https://www.fasel-hd.cam/movie/no-img">
      <img alt="">
    </a>
  </div>
  <div class="postDiv">
    <a href="https://www.fasel-hd.cam/movie/no-alt">
      <img src="https://img.example.com/noalt.jpg">
    </a>
  </div>
</div>
</body></html>"""


class TestFaselhdSpider:
    def test_extract_valid_items(self):
        spider = FaselhdSpider()
        spider._rank_counters["movies"] = 0
        response = _make_response(SAMPLE_HTML)
        items = list(spider.parse(response, "movies", 1))
        assert len(items) == 2, "يجب استخراج عنصرين فقط صحيحين"

    def test_first_item_fields(self):
        spider = FaselhdSpider()
        spider._rank_counters["movies"] = 0
        response = _make_response(SAMPLE_HTML)
        items = list(spider.parse(response, "movies", 1))
        item = items[0]
        assert item["name"] == "فيلم Test"
        assert item["img"] == "https://img.example.com/test.jpg"
        assert item["link"] == "https://www.fasel-hd.cam/movie/test-film"
        assert item["section_key"] == "movies"
        assert item["rank"] == 1
        assert item["slug"] == "test-film"

    def test_data_src_fallback(self):
        """عند عدم وجود data-src يستخدم src"""
        spider = FaselhdSpider()
        spider._rank_counters["movies"] = 0
        response = _make_response(SAMPLE_HTML)
        items = list(spider.parse(response, "movies", 1))
        second = items[1]
        assert second["name"] == "فيلم ثاني"
        assert second["img"] == "https://img.example.com/film2.jpg"
        assert second["rank"] == 2

    def test_skip_empty_alt(self):
        """العناصر بدون alt يتم تخطيها"""
        spider = FaselhdSpider()
        spider._rank_counters["movies"] = 0
        response = _make_response(SAMPLE_HTML)
        items = list(spider.parse(response, "movies", 1))
        names = [i["name"] for i in items]
        assert "" not in names

    def test_skip_empty_img(self):
        """العناصر بدون رابط صورة يتم تخطيها"""
        spider = FaselhdSpider()
        spider._rank_counters["movies"] = 0
        response = _make_response(SAMPLE_HTML)
        items = list(spider.parse(response, "movies", 1))
        imgs = [i["img"] for i in items]
        assert "" not in imgs

    def test_skip_empty_link(self):
        """العناصر بدون رابط يتم تخطيها"""
        spider = FaselhdSpider()
        spider._rank_counters["movies"] = 0
        resp = _make_response("""<div id="postList"><div class="postDiv"><a><img alt="X" data-src="x.jpg"></a></div></div>""")
        items = list(spider.parse(resp, "movies", 1))
        assert len(items) == 0

    def test_empty_page(self):
        """صفحة بدون محتوى ترجع قائمة فارغة"""
        spider = FaselhdSpider()
        spider._rank_counters["tvshows"] = 0
        resp = _make_response("<html></html>")
        items = list(spider.parse(resp, "tvshows", 1))
        assert len(items) == 0

    def test_less_than_24_items_last_page(self):
        """آخر صفحة يمكن أن تحتوي على أقل من 24 عنصر"""
        html = """<div id="postList">"""
        for i in range(5):
            html += f"""<div class="postDiv"><a href="https://x.com/m{i}"><img alt="F{i}" data-src="x{i}.jpg"></a></div>"""
        html += """</div>"""
        spider = FaselhdSpider()
        spider._rank_counters["tvshows"] = 0
        resp = _make_response(html, "https://www.fasel-hd.cam/tvshows/page/7")
        items = list(spider.parse(resp, "tvshows", 7))
        assert len(items) == 5, "آخر صفحة تحتوي على 5 عناصر فقط"

    def test_slug_extraction(self):
        """اختبار استخراج الـ slug من الرابط"""
        spider = FaselhdSpider()
        spider._rank_counters["series"] = 0
        html = """<div id="postList"><div class="postDiv"><a href="https://www.fasel-hd.cam/seasons/%D9%85%D8%B3%D9%84%D8%B3%D9%84-stranger-things"><img alt="مسلسل Stranger Things" data-src="x.jpg"></a></div></div>"""
        resp = _make_response(html)
        items = list(spider.parse(resp, "series", 1))
        assert items[0]["slug"] == "مسلسل-stranger-things"

    @pytest.mark.asyncio
    async def test_start_section_filter(self):
        """تأكيد أن section_filter يحدد الطلبات"""
        spider = FaselhdSpider(section="tvshows")
        spider._rank_counters = {}
        reqs = []
        async for r in spider.start():
            reqs.append(r)
        assert len(reqs) == 7, "tvshows يحتوي على 7 صفحات"

    @pytest.mark.asyncio
    async def test_start_all_sections(self):
        """تأكيد عدد الطلبات الكلي"""
        spider = FaselhdSpider()
        spider._rank_counters = {}
        reqs = []
        async for r in spider.start():
            reqs.append(r)
        total_pages = sum(p for _, _, p in [
            ("movies", "/movies", 582), ("series", "/series", 169),
            ("anime", "/anime", 78), ("asian-series", "/asian-series", 61),
            ("asian-movies", "/asian-movies", 55), ("hindi", "/hindi", 36),
            ("anime-movies", "/anime-movies", 17), ("tvshows", "/tvshows", 7),
        ])
        assert len(reqs) == total_pages
