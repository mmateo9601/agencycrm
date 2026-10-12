from typing import Protocol

from .crawler import CrawlResult, live_crawler
from .errors import CrawlError
from .mock import MockWebsiteProvider


class WebsiteProvider(Protocol):
    def crawl(self, root_url: str, region: str = "ES") -> CrawlResult:
        """Acquire bounded public business facts, without persistence."""
        ...


def website_provider(name: str, config) -> WebsiteProvider:
    """Keep queued mock jobs offline even if the runtime later enables the web adapter."""
    if name == "mock":
        return MockWebsiteProvider()
    if name == "web" and config.WEBSITE_PROVIDER == "web" and config.WEB_CRAWLER_ENABLED:
        return live_crawler(config)
    raise CrawlError("PROVIDER_DISABLED")
