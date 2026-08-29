from __future__ import annotations

import calendar
import hashlib
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import trafilatura
from bs4 import BeautifulSoup

from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from ..utils import parse_datetime, sha256_text
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class ArticleCollector(Collector):
    source = "articles"

    def __init__(self, sources, store, state, preview_chars=4000):  # type: ignore[no-untyped-def]
        super().__init__({"enabled": True}, store, state)
        self.sources = sources
        self.preview_chars = preview_chars

    async def collect(self, now: datetime) -> CollectorResult:
        started = time.monotonic()
        client = SafeHTTPClient(timeout=40)
        items: list[SourceItem] = []
        manifests = []
        errors: list[str] = []
        fetched = 0
        try:
            for source_config in self.sources:
                try:
                    rows, manifest = await self._discover(client, source_config, now)
                    fetched += len(rows)
                    manifests.append(manifest)
                    for row in rows:
                        item = await self._article(client, source_config, row, now)
                        if item:
                            items.append(item)
                except Exception as error:
                    errors.append(
                        f"{source_config.get('id', 'article')}: {type(error).__name__}: {error}"
                    )
        finally:
            await client.close()
        inserted = await self.state.put_items(items)
        for item in items:
            self.store.write_revision(item)
        for manifest in manifests:
            self.store.write_fetch_manifest(manifest)
        status = HealthStatus.SUCCESS
        if errors and items:
            status = HealthStatus.PARTIAL
        elif errors:
            status = HealthStatus.FAILED
        return CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health_from(
                self.source, started, status, fetched, len(items), len(inserted), errors
            ),
        )

    async def _discover(self, client, config, now):  # type: ignore[no-untyped-def]
        url = str(config["url"])
        manifest = new_fetch_manifest(f"articles:{config['id']}", url)
        response = await client.request("GET", url, data_limit=5_000_000)
        suffix = ".xml" if "xml" in response.headers.get("content-type", "") else ".txt"
        blob = self.store.write_blob(response.text, suffix)
        if config.get("kind") == "rss":
            rows = self._rss_rows(response.content, config)
        elif config.get("kind") == "sitemap":
            rows = self._sitemap_rows(response.text, config, now)
        else:
            rows = self._index_rows(response.text, config)
        manifest.blob_refs = [blob]
        return rows, finish_manifest(
            manifest,
            response=response,
            fetched_count=len(rows),
            parsed_count=len(rows),
        )

    def _rss_rows(self, content: bytes, config: dict[str, Any]) -> list[dict[str, Any]]:
        parsed = feedparser.parse(content)
        rows = []
        for entry in list(parsed.entries)[: int(config.get("max_entries", 100))]:
            occurred = _feed_time(entry.get("published_parsed") or entry.get("updated_parsed"))
            rows.append(
                {
                    "url": entry.get("link"),
                    "guid": entry.get("id") or entry.get("guid") or entry.get("link"),
                    "title": " ".join(str(entry.get("title", "")).split()),
                    "author": entry.get("author"),
                    "summary": _feed_text(entry),
                    "occurred_at": occurred,
                    "updated_at": _feed_time(entry.get("updated_parsed")),
                }
            )
        return [row for row in rows if row.get("url")]

    def _sitemap_rows(
        self, text: str, config: dict[str, Any], now: datetime
    ) -> list[dict[str, Any]]:
        root = ET.fromstring(text)
        needles = [value for value in str(config.get("path_contains", "")).split(",") if value]
        rows = []
        for element in root.iter():
            if not element.tag.endswith("url"):
                continue
            location = next((child.text for child in element if child.tag.endswith("loc")), None)
            lastmod = next((child.text for child in element if child.tag.endswith("lastmod")), None)
            if not location or (needles and not any(needle in location for needle in needles)):
                continue
            updated = parse_datetime(lastmod)
            if updated and updated < now - timedelta(days=7):
                continue
            rows.append(
                {
                    "url": location,
                    "guid": location,
                    "title": "",
                    "author": None,
                    "summary": "",
                    "occurred_at": updated,
                    "updated_at": updated,
                }
            )
        return rows[: int(config.get("max_entries", 100))]

    def _index_rows(self, text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        soup = BeautifulSoup(text, "html.parser")
        needles = [value for value in str(config.get("path_contains", "")).split(",") if value]
        allowed = {str(value).lower() for value in config.get("allowed_domains", [])}
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in soup.select("a[href]"):
            url = urljoin(str(config["url"]), str(link.get("href", "")))
            host = (urlparse(url).hostname or "").lower()
            if allowed and host not in allowed:
                continue
            if needles and not any(needle in url for needle in needles):
                continue
            if url in seen:
                continue
            seen.add(url)
            rows.append(
                {
                    "url": url,
                    "guid": url,
                    "title": " ".join(link.get_text(" ", strip=True).split()),
                    "author": None,
                    "summary": "",
                    "occurred_at": None,
                    "updated_at": None,
                }
            )
        return rows[: int(config.get("max_entries", 100))]

    async def _article(self, client, config, row, now):  # type: ignore[no-untyped-def]
        url = str(row["url"])
        host = (urlparse(url).hostname or "").lower()
        allowed = {str(value).lower() for value in config.get("allowed_domains", [])}
        if allowed and host not in allowed:
            return None
        entity = hashlib.sha256(url.encode()).hexdigest()[:24]
        discovery_key = f"article-discovery:{config['id']}:{entity}"
        discovery_value = str(
            row.get("updated_at") or row.get("occurred_at") or row.get("guid") or url
        )
        if await self.state.get_cursor(discovery_key) == discovery_value:
            return None
        response = None
        clean_text = ""
        page_date: datetime | None = None
        raw_refs: list[str] = []
        extraction_error = None
        try:
            response = await client.request("GET", url, data_limit=4_000_000)
            html_ref = self.store.write_blob(response.text, ".html")
            raw_refs.append(html_ref)
            clean_text = (
                trafilatura.extract(
                    response.text,
                    include_comments=False,
                    include_tables=True,
                    favor_precision=True,
                    url=str(response.url),
                )
                or ""
            )
            metadata = trafilatura.extract_metadata(response.text, default_url=str(response.url))
            if metadata and metadata.date:
                page_date = parse_datetime(metadata.date)
        except Exception as error:
            extraction_error = f"{type(error).__name__}: {error}"

        clean_hash = sha256_text(clean_text) if clean_text else "metadata"
        cursor_key = f"article:{config['id']}:{entity}"
        previous_hash = await self.state.get_cursor(cursor_key)
        if previous_hash == clean_hash:
            await self.state.set_cursor(discovery_key, discovery_value)
            return None
        content_status = ContentStatus.FULL if clean_text else ContentStatus.EXTRACTION_FAILED
        if clean_text:
            raw_refs.append(self.store.write_blob(clean_text, ".txt"))
        occurred = row.get("occurred_at") or page_date or now
        updated = row.get("updated_at")
        change = "updated" if previous_hash is not None else "published"
        item = SourceItem(
            item_id=f"article:{config['id']}:{entity}:{clean_hash[:12]}",
            item_type="article",
            source=f"article:{config['id']}",
            surface=str(config.get("role", "editorial")),
            change=change,
            occurred_at=occurred,
            updated_at=updated,
            first_observed_at=now,
            handoff_at=(updated if change == "updated" and updated else occurred),
            time_basis=(
                TimeBasis.UPDATED
                if change == "updated" and updated
                else TimeBasis.OCCURRED
                if row.get("occurred_at") or page_date
                else TimeBasis.OBSERVED
            ),
            content_status=content_status,
            raw_refs=raw_refs,
            payload={
                "source_id": config["id"],
                "source_role": config.get("role", "editorial"),
                "title": row.get("title", ""),
                "author": row.get("author"),
                "url": str(response.url) if response else url,
                "feed_summary": row.get("summary", "")[: self.preview_chars],
                "text_preview": clean_text[: self.preview_chars],
                "full_text_ref": raw_refs[-1] if clean_text else None,
                "clean_text_hash": clean_hash,
                "extraction_error": extraction_error,
            },
        )
        await self.state.set_cursor(cursor_key, clean_hash)
        await self.state.set_cursor(discovery_key, discovery_value)
        return item


def _feed_time(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), UTC)


def _feed_text(entry: Any) -> str:
    content = entry.get("content") or []
    if content:
        return str(content[0].get("value", ""))
    return str(entry.get("summary") or entry.get("description") or "")
