"""Pinned DNS connections with TLS hostname verification and crawl-wide budgets."""

import json
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import ceil

import httpcore
import httpx

from common.security import crawl_url, public_address

from .errors import CrawlError


def retry_delay(value: str) -> int:
    """Honor both delta-seconds and HTTP-date Retry-After values within one day."""
    try:
        seconds = (
            int(value)
            if value.isdecimal()
            else ceil((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        )
        return min(86400, max(1, seconds))
    except TypeError, ValueError, OverflowError:
        return 30


@dataclass
class Budget:
    seconds: float = 60
    max_bytes: int = 2_000_000
    max_requests: int = 54
    delay: float = 1
    started: float = field(default_factory=time.monotonic)
    bytes_read: int = 0
    requests: int = 0
    last_request: float | None = None

    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise CrawlError("TIMEOUT", retryable=True)
        return remaining

    def before_request(self) -> None:
        if self.requests >= self.max_requests:
            raise CrawlError("REQUEST_LIMIT")
        wait = (
            0 if self.last_request is None else self.delay - (time.monotonic() - self.last_request)
        )
        if wait > 0:
            if wait >= self.remaining():
                raise CrawlError("TIMEOUT", retryable=True)
            time.sleep(wait)
        self.remaining()
        self.last_request = time.monotonic()
        self.requests += 1


def resolve_public(host: str, port: int, timeout: float) -> list[str]:
    """Bound OS DNS resolution in a child process, with no shell or URL interpolation."""
    script = (
        "import socket,json,sys; "
        "print(json.dumps(sorted({r[4][0] for r in "
        "socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)})))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, host, str(port)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        addresses = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise CrawlError("TIMEOUT", retryable=True) from exc
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise CrawlError("DNS_FAILED", retryable=True) from exc
    if not addresses:
        raise CrawlError("DNS_FAILED", retryable=True)
    return [public_address(address) for address in addresses]


class DeadlineStream(httpcore.NetworkStream):
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget

    def timeout(self, timeout):
        return min(timeout or self.budget.remaining(), self.budget.remaining())

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout=self.timeout(timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout=self.timeout(timeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return DeadlineStream(
            self.stream.start_tls(
                ssl_context, server_hostname=server_hostname, timeout=self.timeout(timeout)
            ),
            self.budget,
        )

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class PublicNetworkBackend(httpcore.NetworkBackend):
    def __init__(self, budget):
        self.budget = budget

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = resolve_public(host, port, min(timeout or 10, self.budget.remaining()))
        stream = httpcore.SyncBackend().connect_tcp(
            addresses[0],
            port,
            timeout=min(timeout or 10, self.budget.remaining()),
            socket_options=socket_options,
        )
        try:
            peer = stream.get_extra_info("server_addr")
            if not peer or public_address(peer[0]) != addresses[0]:
                raise CrawlError("UNSAFE_ADDRESS")
        except Exception:
            stream.close()
            raise
        return DeadlineStream(stream, self.budget)

    def connect_unix_socket(self, *args, **kwargs):
        raise CrawlError("UNSAFE_ADDRESS")


class ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        yield from self.stream

    def close(self):
        self.stream.close()


class PublicTransport(httpx.BaseTransport):
    def __init__(self, budget):
        self.pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PublicNetworkBackend(budget),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
        )

    def handle_request(self, request):
        response = self.pool.handle_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=ResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self):
        self.pool.close()


@dataclass(frozen=True)
class Page:
    status: int
    text: str = ""
    location: str = ""


class SafeHTTPClient:
    def __init__(
        self,
        budget: Budget,
        *,
        user_agent="AgencyCRM-Bot/1.0",
        timeout=10,
        transport_factory=PublicTransport,
    ):
        self.budget, self.user_agent, self.timeout = budget, user_agent, timeout
        self.transport_factory = transport_factory

    def get(self, url: str) -> Page:
        """One bounded GET; callers explicitly authorize and follow every redirect."""
        url = crawl_url(url)
        self.budget.before_request()
        try:
            # A fresh client prevents cookie-based sessions and connection reuse across DNS checks.
            with httpx.Client(
                transport=self.transport_factory(self.budget),
                trust_env=False,
                follow_redirects=False,
                timeout=min(self.timeout, self.budget.remaining()),
            ) as client:
                with client.stream(
                    "GET",
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,text/plain",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code == 429:
                        value = response.headers.get("retry-after", "30")
                        delay = retry_delay(value)
                        raise CrawlError("RATE_LIMIT", retryable=True, retry_after=delay)
                    if response.status_code >= 500:
                        raise CrawlError("SERVER_ERROR", retryable=True)
                    if 300 <= response.status_code < 400:
                        return Page(
                            response.status_code, location=response.headers.get("location", "")
                        )
                    if response.status_code != 200:
                        return Page(response.status_code)
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime not in {"text/html", "text/plain", "application/xhtml+xml"}:
                        raise CrawlError("CONTENT_TYPE")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise CrawlError("CONTENT_ENCODING")
                    chunks = []
                    for chunk in response.iter_raw():
                        self.budget.remaining()
                        self.budget.bytes_read += len(chunk)
                        if self.budget.bytes_read > self.budget.max_bytes:
                            raise CrawlError("BYTE_LIMIT")
                        chunks.append(chunk)
                    self.budget.remaining()
                    return Page(200, b"".join(chunks).decode("utf-8", errors="replace"))
        except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException) as exc:
            raise CrawlError("TIMEOUT", retryable=True) from exc
        except (httpx.HTTPError, httpcore.NetworkError, httpcore.ProtocolError, OSError) as exc:
            raise CrawlError("HTTP_FAILED", retryable=True) from exc
