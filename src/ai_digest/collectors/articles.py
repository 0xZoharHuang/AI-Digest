from __future__ import annotations

import asyncio
import calendar
import hashlib
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import feedparser
import trafilatura
from bs4 import BeautifulSoup

from ..models import (
    CollectorResult,
    ContentStatus,
    HealthStatus,
    ObservationKind,
    SourceItem,
    TimeBasis,
)
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
        cursor_updates: dict[str, str | None] = {}
        extraction_failures: dict[str, int] = {}
        source_stats: dict[str, dict[str, Any]] = {}
        fetched = 0
        successful_sources = 0
        try:
            for source_config in self.sources:
                source_id = str(source_config.get("id", "article"))
                source_stats[source_id] = {
                    "status": "running",
                    "discovered": 0,
                    "materialized": 0,
                    "extraction_failures": 0,
                    "error": None,
                }
                try:
                    rows, manifest = await self._discover(client, source_config, now)
                    source_stats[source_id]["discovered"] = len(rows)
                    fetched += len(rows)
                    manifests.append(manifest)
                    semaphore = asyncio.Semaphore(
                        int(source_config.get("article_concurrency", 5))
                    )

                    async def load(  # type: ignore[no-untyped-def]
                        row,
                        semaphore=semaphore,
                        source_config=source_config,
                    ):
                        async with semaphore:
                            return await self._article(client, source_config, row, now)

                    outcomes = await asyncio.gather(
                        *(load(row) for row in rows), return_exceptions=True
                    )
                    for row, outcome in zip(rows, outcomes, strict=True):
                        if isinstance(outcome, BaseException):
                            errors.append(
                                f"{source_config.get('id', 'article')}:{row.get('url')}: "
                                f"{type(outcome).__name__}: {outcome}"
                            )
                            continue
                        item, updates = outcome
                        cursor_updates.update(updates)
                        if item:
                            items.append(item)
                            source_stats[source_id]["materialized"] += 1
                            if item.payload.get("extraction_error"):
                                extraction_failures[source_id] = (
                                    extraction_failures.get(source_id, 0) + 1
                                )
                                source_stats[source_id]["extraction_failures"] += 1
                    source_stats[source_id]["status"] = (
                        "partial"
                        if source_stats[source_id]["extraction_failures"]
                        else "success"
                    )
                    cursor_updates[f"article-source:{source_id}:initialized"] = now.isoformat()
                    successful_sources += 1
                except Exception as error:
                    source_stats[source_id]["status"] = "failed"
                    source_stats[source_id]["error"] = f"{type(error).__name__}: {error}"
                    errors.append(
                        f"{source_config.get('id', 'article')}: {type(error).__name__}: {error}"
                    )
        finally:
            await client.close()
        errors.extend(
            f"{source_id}: {count} article body extraction failure(s); metadata preserved"
            for source_id, count in sorted(extraction_failures.items())
        )
        for item in items:
            self.store.write_revision(item)
        for manifest in manifests:
            self.store.write_fetch_manifest(manifest)
        inserted = await self.state.put_items(items)
        await self.state.set_cursors(cursor_updates)
        status = HealthStatus.SUCCESS
        if errors and successful_sources:
            status = HealthStatus.PARTIAL
        elif errors:
            status = HealthStatus.FAILED
        result = CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health_from(
                self.source, started, status, fetched, len(items), len(inserted), errors
            ),
        )
        result.health.surfaces = source_stats
        result.health.raw_receipts_complete = not bool(extraction_failures)
        return result

    async def _discover(self, client, config, now):  # type: ignore[no-untyped-def]
        url = str(config["url"])
        manifest = new_fetch_manifest(f"articles:{config['id']}", url)
        response = await client.request("GET", url, data_limit=5_000_000)
        suffix = ".xml" if "xml" in response.headers.get("content-type", "") else ".txt"
        blob = self.store.write_blob(response.text, suffix)
        blob_refs = [blob]
        if config.get("kind") == "rss":
            rows = self._rss_rows(response.content, config)
        elif config.get("kind") == "sitemap":
            root = ET.fromstring(response.text)
            if root.tag.endswith("sitemapindex"):
                rows = []
                sitemap_urls = [
                    child.text
                    for element in root
                    for child in element
                    if child.tag.endswith("loc") and child.text
                ][: int(config.get("sitemap_child_limit", 20))]
                for child_url in sitemap_urls:
                    child_response = await client.request(
                        "GET", str(child_url), data_limit=5_000_000
                    )
                    blob_refs.append(self.store.write_blob(child_response.text, ".xml"))
                    rows.extend(
                        self._sitemap_rows(
                            child_response.text, {**config, "allow_empty": True}, now
                        )
                    )
                rows.sort(
                    key=lambda row: row.get("updated_at")
                    or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )
                rows = rows[: int(config.get("max_entries", 100))]
            else:
                rows = self._sitemap_rows(response.text, config, now)
        else:
            rows = self._index_rows(response.text, config)
        if (
            not rows
            and config.get("kind") != "sitemap"
            and not bool(config.get("allow_empty", False))
        ):
            raise RuntimeError("discovery returned zero rows")
        manifest.blob_refs = blob_refs
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
        matched = 0
        for element in root.iter():
            if not element.tag.endswith("url"):
                continue
            location = next((child.text for child in element if child.tag.endswith("loc")), None)
            lastmod = next((child.text for child in element if child.tag.endswith("lastmod")), None)
            if not location or (needles and not any(needle in location for needle in needles)):
                continue
            matched += 1
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
        if matched == 0 and not bool(config.get("allow_empty", False)):
            raise RuntimeError("sitemap contained zero matching URLs")
        rows.sort(
            key=lambda row: row.get("updated_at") or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return rows[: int(config.get("max_entries", 100))]

    def _index_rows(self, text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        soup = BeautifulSoup(text, "html.parser")
        needles = [value for value in str(config.get("path_contains", "")).split(",") if value]
        allowed = {str(value).lower() for value in config.get("allowed_domains", [])}
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        selector = str(config.get("link_selector", "a[href]"))
        for link in soup.select(selector):
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
        url = _canonical_url(str(row["url"]))
        host = (urlparse(url).hostname or "").lower()
        allowed = {str(value).lower() for value in config.get("allowed_domains", [])}
        if allowed and host not in allowed:
            return None, {}
        entity = hashlib.sha256(url.encode()).hexdigest()[:24]
        discovery_key = f"article-discovery:{config['id']}:{entity}"
        discovery_value = (
            f"{url}:{now.date().isoformat()}"
            if config.get("kind") == "index"
            else str(row.get("updated_at") or row.get("occurred_at") or row.get("guid") or url)
        )
        if await self.state.get_cursor(discovery_key) == discovery_value:
            return None, {}
        source_time = row.get("updated_at") or row.get("occurred_at")
        if (
            isinstance(source_time, datetime)
            and source_time < now - timedelta(days=int(config.get("max_age_days", 7)))
        ):
            return None, {discovery_key: discovery_value}
        response = None
        clean_text = ""
        page_date: datetime | None = None
        raw_refs: list[str] = []
        extraction_error = None
        try:
            response = await client.request("GET", url, data_limit=4_000_000)
            html_ref = self.store.write_blob(response.text, ".html")
            raw_refs.append(html_ref)
            content_selector = config.get("content_selector")
            if content_selector:
                soup = BeautifulSoup(response.text, "html.parser")
                content = soup.select_one(str(content_selector))
                if content is None:
                    raise ValueError(f"content selector did not match: {content_selector}")
                for unwanted in content.select("script,style,noscript"):
                    unwanted.decompose()
                clean_text = "\n".join(
                    line.strip()
                    for line in content.get_text("\n", strip=True).splitlines()
                    if line.strip()
                )
            else:
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
            return None, {discovery_key: discovery_value}
        content_status = (
            ContentStatus.FULL
            if clean_text
            else ContentStatus.PREVIEW
            if row.get("summary") or row.get("title")
            else ContentStatus.EXTRACTION_FAILED
        )
        if clean_text:
            raw_refs.append(self.store.write_blob(clean_text, ".txt"))
        occurred = row.get("occurred_at") or page_date or now
        updated = row.get("updated_at")
        change = "updated" if previous_hash is not None else "published"
        bootstrap_age = timedelta(hours=int(config.get("bootstrap_max_age_hours", 24)))
        initialized = await self.state.get_cursor(
            f"article-source:{config['id']}:initialized"
        )
        observation_kind = (
            ObservationKind.CONTENT_REVISION
            if previous_hash is not None
            else ObservationKind.BOOTSTRAP_SNAPSHOT
            if occurred < now - bootstrap_age and initialized is None
            else ObservationKind.LATE_ARRIVAL
            if occurred < now - bootstrap_age
            else ObservationKind.LIVE_INCREMENT
        )
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
            ready_at=now,
            observation_kind=observation_kind,
            entity_key=f"article:{config['id']}:{entity}",
            content_hash=clean_hash,
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
        if not clean_text:
            return item, {}
        return item, {cursor_key: clean_hash, discovery_key: discovery_value}


def _feed_time(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), UTC)


def _feed_text(entry: Any) -> str:
    content = entry.get("content") or []
    if content:
        return str(content[0].get("value", ""))
    return str(entry.get("summary") or entry.get("description") or "")


def _canonical_url(value: str) -> str:
    parsed = urlparse(value)
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"ref", "source", "fbclid", "gclid"}
        ]
    )
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))
