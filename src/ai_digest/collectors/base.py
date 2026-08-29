from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import CollectorResult, FetchManifest, HealthStatus, SourceHealth
from ..store import FileStore, StateDB
from ..utils import redact_mapping

RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class Collector(ABC):
    source: str

    def __init__(self, config: dict[str, Any], store: FileStore, state: StateDB):
        self.config = config
        self.store = store
        self.state = state

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @abstractmethod
    async def collect(self, now: datetime) -> CollectorResult: ...

    def disabled(self) -> CollectorResult:
        return CollectorResult(
            source=self.source,
            health=SourceHealth(source=self.source, status=HealthStatus.DISABLED),
        )


class SafeHTTPClient:
    def __init__(self, *, timeout: float = 30, max_attempts: int = 3):
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "AI-Intelligence-Radar/0.2 (+local research)"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data_limit: int | None = None,
        validate_public_url: bool = False,
        max_redirects: int = 5,
    ) -> httpx.Response:
        current = url
        for redirect in range(max_redirects + 1):
            if validate_public_url:
                await validate_public_http_url(current)
            response = await self._request_with_retry(
                method, current, headers=headers, params=params if redirect == 0 else None
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                if data_limit is not None and len(response.content) > data_limit:
                    raise ValueError(f"response exceeds {data_limit} bytes: {current}")
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
        raise ValueError(f"too many redirects: {url}")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = await self.client.request(method, url, headers=headers, params=params)
                if response.status_code not in RETRYABLE:
                    response.raise_for_status()
                    return response
                if attempt == self.max_attempts - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                await asyncio.sleep(min(wait, 60))
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                if attempt == self.max_attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)
        assert last_error is not None
        raise last_error


async def validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"unsupported URL: {url}")
    infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    proxy_fake_network = ipaddress.ip_network("198.18.0.0/15")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        (".local", ".internal")
    ):
        raise ValueError(f"local hostname is not allowed: {hostname}")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address in proxy_fake_network and not _is_ip_literal(hostname):
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError(f"URL resolves to non-public address: {parsed.hostname}")


async def fetch_external_metadata(client: SafeHTTPClient, url: str) -> dict[str, Any]:
    response = await client.request(
        "GET", url, validate_public_url=True, data_limit=512_000, max_redirects=5
    )
    content_type = response.headers.get("content-type", "")
    title = ""
    description = ""
    if "html" in content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:500]
        meta = soup.select_one('meta[property="og:description"],meta[name="description"]')
        description = str(meta.get("content", ""))[:1000] if meta else ""
    return {
        "requested_url": url,
        "final_url": str(response.url),
        "content_type": content_type,
        "title": title,
        "description": description,
    }


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def new_fetch_manifest(source: str, url: str, started: datetime | None = None) -> FetchManifest:
    moment = started or datetime.now(UTC)
    return FetchManifest(
        fetch_id=f"{moment.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
        source=source,
        started_at=moment,
        completed_at=moment,
        request={"method": "GET", "url": url},
    )


def finish_manifest(
    manifest: FetchManifest,
    *,
    response: httpx.Response | None = None,
    fetched_count: int = 0,
    parsed_count: int = 0,
    status: HealthStatus = HealthStatus.SUCCESS,
    errors: list[str] | None = None,
) -> FetchManifest:
    manifest.completed_at = datetime.now(UTC)
    manifest.fetched_count = fetched_count
    manifest.parsed_count = parsed_count
    manifest.status = status
    manifest.errors = errors or []
    if response is not None:
        manifest.response_status = response.status_code
        allowed = {"etag", "last-modified", "content-type", "content-length", "date"}
        manifest.response_headers = redact_mapping(
            {key: value for key, value in response.headers.items() if key.lower() in allowed}
        )
    return manifest


def health_from(
    source: str,
    started: float,
    status: HealthStatus,
    fetched: int,
    parsed: int,
    new: int,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> SourceHealth:
    return SourceHealth(
        source=source,
        status=status,
        fetched_count=fetched,
        parsed_count=parsed,
        new_count=new,
        duration_seconds=time.monotonic() - started,
        errors=errors or [],
        warnings=warnings or [],
    )
