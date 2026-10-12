"""Sequential same-origin crawling with robots enforcement before each page hop."""

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from common.security import crawl_url

from .errors import CrawlError
from .extraction import extract, private_path
from .http import Budget, SafeHTTPClient


@dataclass
class CrawlResult:
    status_code: int | None = None
    title: str = ""
    meta_description: str = ""
    has_contact_page: bool = False
    has_privacy_page: bool = False
    has_booking: bool = False
    has_ecommerce: bool = False
    detected_emails: list = field(default_factory=list)
    detected_phones: list = field(default_factory=list)
    detected_social_urls: list = field(default_factory=list)
    pages_crawled: int = 0
    findings: dict = field(default_factory=dict)


class WebsiteCrawler:
    def __init__(self, client: SafeHTTPClient, *, max_pages: int = 8):
        self.client, self.max_pages = client, max_pages
        self.robots = {}

    def target(self, url: str, root: str) -> str:
        target = crawl_url(url)
        if urlsplit(target).hostname != urlsplit(root).hostname:
            raise CrawlError("CROSS_HOST_REDIRECT")
        if urlsplit(root).scheme == "https" and urlsplit(target).scheme != "https":
            raise CrawlError("HTTPS_DOWNGRADE")
        if private_path(target):
            raise CrawlError("PRIVATE_PATH")
        return target

    def allowed(self, url: str) -> None:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            robot_url = origin + "/robots.txt"
            for hop in range(6):
                page = self.client.get(robot_url)
                if 300 <= page.status < 400:
                    if hop == 5 or not page.location:
                        raise CrawlError("REDIRECT_LIMIT")
                    next_url = self.target(urljoin(robot_url, page.location), robot_url)
                    if urlsplit(next_url)[:2] != urlsplit(robot_url)[:2]:
                        raise CrawlError("ROBOTS_REDIRECT")
                    robot_url = next_url
                    continue
                break
            parser = RobotFileParser()
            if page.status == 404:
                parser.parse([])
            elif page.status == 200:
                parser.parse(page.text.splitlines())
            else:
                raise CrawlError("ROBOTS_UNAVAILABLE")
            self.robots[origin] = parser
        parser = self.robots[origin]
        agent = self.client.user_agent
        if not parser.can_fetch(agent, url):
            raise CrawlError("ROBOTS_DENIED")
        delay = parser.crawl_delay(agent) or 0
        rate = parser.request_rate(agent)
        if rate:
            delay = max(delay, rate.seconds / rate.requests)
        self.client.budget.delay = max(self.client.budget.delay, delay)

    def crawl(self, root_url: str, region: str = "ES") -> CrawlResult:
        """Return facts only; stop on policy/network failures without persisting partial HTML."""
        root = self.target(root_url, root_url)
        result = CrawlResult()
        queue, visited = [root], set()
        sources, socials = {}, {}
        while queue and result.pages_crawled < self.max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            for hop in range(6):
                url = self.target(url, root)
                self.allowed(url)
                page = self.client.get(url)
                if 300 <= page.status < 400:
                    if hop == 5 or not page.location:
                        raise CrawlError("REDIRECT_LIMIT")
                    url = urljoin(url, page.location)
                    continue
                break
            visited.add(url)
            if result.status_code is None:
                result.status_code = page.status
            if page.status != 200:
                raise CrawlError("PAGE_UNAVAILABLE")
            data = extract(page.text, url, region)
            self.client.budget.remaining()
            if result.pages_crawled == 0:
                result.title, result.meta_description = data["title"], data["meta_description"]
            result.pages_crawled += 1
            for name in ("has_contact_page", "has_privacy_page", "has_booking", "has_ecommerce"):
                setattr(result, name, getattr(result, name) or data[name])
            for name, key in (("detected_emails", "emails"), ("detected_phones", "phones")):
                values = getattr(result, name)
                for value in data[key]:
                    if value not in values and len(values) < 10:
                        values.append(value)
                        sources[value] = url
            for target, platform in data["socials"].items():
                if len(socials) < 10:
                    socials[target] = {"url": target, "platform": platform, "source_url": url}
            result.findings["analytics_observed"] = (
                result.findings.get("analytics_observed", False) or data["analytics_observed"]
            )
            result.findings["contact_form_observed"] = (
                result.findings.get("contact_form_observed", False) or data["contact_form_observed"]
            )
            queue.extend(
                link for link in data["links"] if link not in visited and link not in queue
            )
        result.detected_social_urls = list(socials.values())
        result.findings.update(
            sources=sources,
            requests=self.client.budget.requests,
            bytes_read=self.client.budget.bytes_read,
        )
        return result


def live_crawler(settings) -> WebsiteCrawler:
    """Build a bounded crawler only after the service checks explicit activation."""
    budget = Budget(
        seconds=min(120, max(1, settings.WEB_CRAWLER_TOTAL_SECONDS)),
        max_bytes=min(2_000_000, max(1, settings.WEB_CRAWLER_MAX_BYTES)),
        delay=max(0.1, settings.WEB_CRAWLER_DELAY_SECONDS),
    )
    return WebsiteCrawler(
        SafeHTTPClient(
            budget,
            user_agent=settings.WEB_CRAWLER_USER_AGENT,
            timeout=min(30, max(1, settings.WEB_CRAWLER_TIMEOUT_SECONDS)),
        ),
        max_pages=min(8, max(1, settings.WEB_CRAWLER_MAX_PAGES_PER_DOMAIN)),
    )
