from pathlib import Path
from urllib.parse import urlsplit

from .crawler import CrawlResult, WebsiteCrawler
from .errors import CrawlError
from .http import Budget, Page

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureClient:
    user_agent = "AgencyCRM-Bot/1.0"

    def __init__(self):
        self.budget = Budget(delay=0)

    def get(self, url: str) -> Page:
        self.budget.before_request()
        if urlsplit(url).path == "/robots.txt":
            return Page(200, "User-agent: *\nDisallow: /private\n")
        filename = "home.html" if urlsplit(url).path == "/" else "contact.html"
        html = (FIXTURES / filename).read_text(encoding="utf-8")
        self.budget.bytes_read += len(html.encode())
        return Page(200, html)


def mock_crawl(url: str, region: str) -> CrawlResult:
    """Only fictional .invalid companies may receive fictional contact facts."""
    if not (urlsplit(url).hostname or "").endswith(".invalid"):
        raise CrawlError("MOCK_REQUIRES_INVALID_DOMAIN")
    return WebsiteCrawler(FixtureClient()).crawl(url, region)


class MockWebsiteProvider:
    def crawl(self, root_url: str, region: str = "ES") -> CrawlResult:
        """Run the same crawler/extractors over local fixtures, without sockets or DNS."""
        return mock_crawl(root_url, region)
