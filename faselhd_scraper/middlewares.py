import logging

logger = logging.getLogger(__name__)


class FaselhdDownloaderMiddleware:
    def process_request(self, request, spider):
        return None

    def process_response(self, request, response, spider):
        if response.status in (429, 503):
            spider.logger.warning(f"Rate limited on {request.url} — status {response.status}")
        return response
