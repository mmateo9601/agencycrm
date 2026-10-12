import json
import subprocess
from unittest.mock import Mock

import httpcore
import httpx
import pytest

from common.security import crawl_url, public_address
from integrations.websites.crawler import WebsiteCrawler
from integrations.websites.errors import CrawlError
from integrations.websites.extraction import extract
from integrations.websites.http import (
    Budget,
    DeadlineStream,
    PublicNetworkBackend,
    PublicTransport,
    SafeHTTPClient,
    resolve_public,
)
from integrations.websites.mock import FIXTURES, mock_crawl


def scripted_client(routes, *, budget=None):
    calls = []

    def handle(request):
        calls.append(request)
        value = routes.get(str(request.url), (404, ""))
        if isinstance(value, Exception):
            raise value
        status, body, *headers = value
        return httpx.Response(
            status,
            headers={"content-type": "text/html", **(headers[0] if headers else {})},
            stream=httpx.ByteStream(body.encode()),
        )

    client = SafeHTTPClient(
        budget or Budget(delay=0), transport_factory=lambda budget: httpx.MockTransport(handle)
    )
    return client, calls


def test_crawler_rejects_private_ip_targets():
    for address in (
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "[::1]",
        "[fc00::1]",
        "[fe80::1]",
        "[::ffff:127.0.0.1]",
        "[64:ff9b::a00:1]",
        "[2002:7f00:1::1]",
    ):
        client, calls = scripted_client({})
        with pytest.raises(CrawlError):
            WebsiteCrawler(client).crawl(f"http://{address}/")
        assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "http://localhost",
        "https://user:pass@example.com",
        "https://example.com:8080",
        "https://example.com/?token=secret",
        "https://example.com\\@127.0.0.1",
        "https://example.com\n",
        "https://a.local",
        "https://%31.com",
    ],
)
def test_unsafe_url_syntax(url):
    with pytest.raises(CrawlError):
        crawl_url(url)


def test_dns_resolution_rejects_any_private_answer_and_is_bounded(monkeypatch):
    run = Mock(return_value=Mock(stdout=json.dumps(["8.8.8.8", "127.0.0.1"])))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(CrawlError, match="política"):
        resolve_public("example.com", 443, 0.5)
    assert run.call_args.kwargs["timeout"] == 0.5
    assert "shell" not in run.call_args.kwargs
    run.return_value.stdout = '["8.8.8.8"]'
    assert resolve_public("example.com", 443, 1) == ["8.8.8.8"]
    run.side_effect = subprocess.TimeoutExpired("dns", 1)
    with pytest.raises(CrawlError) as failure:
        resolve_public("example.com", 443, 1)
    assert failure.value.retryable


def test_pinned_connection_preserves_tls_hostname_and_checks_peer(monkeypatch):
    resolver = Mock(return_value=["8.8.8.8"])
    monkeypatch.setattr("integrations.websites.http.resolve_public", resolver)
    stream = Mock()
    stream.get_extra_info.return_value = ("8.8.8.8", 443)
    connect = Mock(return_value=stream)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    backend = PublicNetworkBackend(Budget())
    wrapped = backend.connect_tcp("example.com", 443, timeout=3)
    assert connect.call_args.args[:2] == ("8.8.8.8", 443)
    wrapped.start_tls(Mock(), server_hostname="example.com", timeout=3)
    assert stream.start_tls.call_args.kwargs["server_hostname"] == "example.com"
    # Each connection resolves again; a DNS rebinding answer never reaches the backend socket.
    resolver.side_effect = CrawlError("UNSAFE_ADDRESS")
    with pytest.raises(CrawlError):
        backend.connect_tcp("example.com", 443)
    assert connect.call_count == 1
    resolver.side_effect = None
    stream.get_extra_info.return_value = ("127.0.0.1", 443)
    with pytest.raises(CrawlError):
        backend.connect_tcp("example.com", 443)
    stream.close.assert_called_once()


def test_transport_uses_original_host_and_streams_response():
    transport = PublicTransport(Budget())
    transport.pool.close()
    transport.pool = Mock()
    stream = Mock()
    stream.__iter__ = Mock(return_value=iter([b"ok"]))
    transport.pool.handle_request.return_value = Mock(
        status=200, headers=[], stream=stream, extensions={}
    )
    response = transport.handle_request(httpx.Request("GET", "https://example.com/contacto"))
    request = transport.pool.handle_request.call_args.args[0]
    assert request.url.host == b"example.com"
    assert response.read() == b"ok"
    response.close()
    stream.close.assert_called()
    transport.close()


def test_deadline_covers_slow_reads(monkeypatch):
    budget = Budget(seconds=1)
    stream = Mock()
    wrapped = DeadlineStream(stream, budget)
    wrapped.write(b"GET", timeout=10)
    assert stream.write.call_args.kwargs["timeout"] <= 1
    monkeypatch.setattr("integrations.websites.http.time.monotonic", lambda: budget.started + 2)
    with pytest.raises(CrawlError):
        wrapped.read(1024, timeout=10)
    stream.read.assert_not_called()


@pytest.mark.parametrize(
    "headers,body,code",
    [
        ({"content-type": "image/png"}, "image", "CONTENT_TYPE"),
        ({"content-encoding": "gzip"}, "compressed", "CONTENT_ENCODING"),
        ({}, "too large", "BYTE_LIMIT"),
    ],
)
def test_http_rejects_mime_compression_and_oversized_stream(headers, body, code):
    client, calls = scripted_client(
        {"https://example.com/": (200, body, headers)}, budget=Budget(max_bytes=4, delay=0)
    )
    with pytest.raises(CrawlError) as failure:
        client.get("https://example.com/")
    assert failure.value.code == code
    assert calls[0].headers["accept-encoding"] == "identity"
    assert "cookie" not in calls[0].headers and "authorization" not in calls[0].headers


@pytest.mark.parametrize(
    "status,headers,code", [(429, {"retry-after": "90"}, "RATE_LIMIT"), (503, {}, "SERVER_ERROR")]
)
def test_http_retry_errors(status, headers, code):
    client, _ = scripted_client({"https://example.com/": (status, "secret", headers)})
    with pytest.raises(CrawlError) as failure:
        client.get("https://example.com/")
    assert failure.value.code == code and failure.value.retryable
    if status == 429:
        assert failure.value.retry_after == 90
    assert "secret" not in str(failure.value)


def test_fixture_extraction_and_limits():
    result = mock_crawl("https://clinic.example.invalid/", "ES")
    assert result.detected_emails == ["info@clinic.example.invalid"]
    assert result.detected_phones == ["+34981000000"]
    assert result.detected_social_urls[0]["platform"] == "INSTAGRAM"
    assert result.pages_crawled == 3
    assert result.has_contact_page and result.has_privacy_page and result.has_booking
    assert result.findings["contact_form_observed"]
    assert "secret" not in str(result) and "<html" not in str(result)
    html = (FIXTURES / "home.html").read_text(encoding="utf-8")
    client, calls = scripted_client({"https://example.com/": (200, html)})
    assert WebsiteCrawler(client, max_pages=1).crawl("https://example.com/").pages_crawled == 1
    assert len(calls) == 2  # robots plus one page


def test_robots_denies_before_page_or_redirect_request():
    routes = {
        "https://example.com/robots.txt": (200, "User-agent: *\nDisallow: /private"),
        "https://example.com/": (302, "", {"location": "/private"}),
    }
    client, calls = scripted_client(routes)
    with pytest.raises(CrawlError) as failure:
        WebsiteCrawler(client).crawl("https://example.com/")
    assert failure.value.code == "ROBOTS_DENIED"
    assert len(calls) == 2
    routes["https://example.com/robots.txt"] = (403, "")
    client, calls = scripted_client(routes)
    with pytest.raises(CrawlError) as failure:
        WebsiteCrawler(client).crawl("https://example.com/")
    assert failure.value.code == "ROBOTS_UNAVAILABLE"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/",
        "https://other.example.com/",
        "http://example.com/",
        "/login",
        "https://u:p@example.com/",
    ],
)
def test_redirect_targets_revalidated(location):
    client, calls = scripted_client({"https://example.com/": (302, "", {"location": location})})
    with pytest.raises(CrawlError):
        WebsiteCrawler(client).crawl("https://example.com/")
    assert len(calls) == 2


def test_redirect_limit_and_global_bytes():
    client, calls = scripted_client({"https://example.com/": (301, "", {"location": "/"})})
    with pytest.raises(CrawlError) as failure:
        WebsiteCrawler(client).crawl("https://example.com/")
    assert failure.value.code == "REDIRECT_LIMIT" and len(calls) == 7
    client, calls = scripted_client(
        {
            "https://example.com/robots.txt": (200, "#123456"),
            "https://example.com/": (200, "123456"),
        },
        budget=Budget(max_bytes=10, delay=0),
    )
    with pytest.raises(CrawlError) as failure:
        WebsiteCrawler(client).crawl("https://example.com/")
    assert failure.value.code == "BYTE_LIMIT"


def test_robots_crawl_delay_and_request_budget(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("integrations.websites.http.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "integrations.websites.http.time.sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    client, calls = scripted_client(
        {
            "https://example.com/robots.txt": (200, "User-agent: *\nCrawl-delay: 3"),
            "https://example.com/": (200, "Hello"),
        },
        budget=Budget(started=100, delay=0),
    )
    WebsiteCrawler(client).crawl("https://example.com/")
    assert now[0] == 103
    client.budget.max_requests = 2
    with pytest.raises(CrawlError) as failure:
        client.get("https://example.com/")
    assert failure.value.code == "REQUEST_LIMIT"


def test_external_links_and_forms_are_never_followed():
    data = extract(
        '<a href="https://evil.example.com/contact">Contact</a>'
        '<a href="/contact?delete=1">Delete</a><form action="/send"></form>'
        '<a href="/contact">Contact</a>',
        "https://example.com/",
        "ES",
    )
    assert data["links"] == ["https://example.com/contact"]
    assert public_address("8.8.8.8") == "8.8.8.8"


def test_retry_after_http_date():
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    from integrations.websites.http import retry_delay

    assert 59 <= retry_delay(format_datetime(datetime.now(UTC) + timedelta(seconds=60))) <= 60
    assert retry_delay("invalid") == 30


@pytest.mark.parametrize("url", ["https://example.com/%6cogin", "https://example.com/%2561dmin"])
def test_encoded_private_paths_are_blocked(url):
    client, calls = scripted_client({})
    with pytest.raises(CrawlError) as failure:
        WebsiteCrawler(client).crawl(url)
    assert failure.value.code == "PRIVATE_PATH" and not calls


def test_provider_activation_and_mock_boundary(settings):
    from integrations.websites.factory import website_provider

    with pytest.raises(CrawlError):
        website_provider("web", settings)
    with pytest.raises(CrawlError):
        website_provider("mock", settings).crawl("https://example.com/")
    settings.WEBSITE_PROVIDER, settings.WEB_CRAWLER_ENABLED = "web", True
    assert isinstance(website_provider("web", settings), WebsiteCrawler)
    assert (
        website_provider("mock", settings).crawl("https://demo.example.invalid/").pages_crawled == 3
    )


@pytest.mark.parametrize(
    "error,code",
    [(httpcore.ReadTimeout(), "TIMEOUT"), (httpx.ConnectError("secret"), "HTTP_FAILED")],
)
def test_transport_errors_are_sanitized(error, code):
    client, _ = scripted_client({"https://example.com/": error})
    with pytest.raises(CrawlError) as failure:
        client.get("https://example.com/")
    assert failure.value.code == code
    assert "secret" not in str(failure.value)


def test_robots_redirect_and_excessive_delay():
    client, calls = scripted_client(
        {
            "https://example.com/robots.txt": (302, "", {"location": "/robot-rules"}),
            "https://example.com/robot-rules": (200, "User-agent: *\nCrawl-delay: 300"),
        }
    )
    with pytest.raises(CrawlError) as failure:
        WebsiteCrawler(client).crawl("https://example.com/")
    assert failure.value.code == "TIMEOUT" and len(calls) == 2
