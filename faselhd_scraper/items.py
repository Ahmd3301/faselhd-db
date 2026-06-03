import scrapy


class FaselhdItem(scrapy.Item):
    section_key = scrapy.Field()
    rank = scrapy.Field()
    slug = scrapy.Field()
    name = scrapy.Field()
    img = scrapy.Field()
    link = scrapy.Field()
