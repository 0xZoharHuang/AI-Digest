from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import feedparser

from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from ..utils import parse_datetime
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class ArxivCollector(Collector):
    source = "arxiv"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        categories = "+".join(self.config.get("categories", []))
        url = str(self.config.get("rss_url", "")).format(categories=categories)
        client = SafeHTTPClient(timeout=60)
        manifest = new_fetch_manifest(self.source, url)
        errors: list[str] = []
        items: list[SourceItem] = []
        fetched = 0
        response = None
        try:
            response = await client.request("GET", url)
            blob = self.store.write_blob(response.text, ".xml")
            parsed = feedparser.parse(response.content)
            entries = list(parsed.entries)
            if parsed.bozo:
                errors.append(f"feed_parse: {parsed.bozo_exception}")
            fetched = len(entries)
            for entry in entries:
                item = self._entry(entry, blob, now)
                if item:
                    items.append(item)
            manifest.blob_refs = [blob]
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            await client.close()
        for item in items:
            self.store.write_revision(item)
        status = (
            HealthStatus.SUCCESS
            if not errors
            else HealthStatus.PARTIAL
            if items
            else HealthStatus.FAILED
        )
        manifest = finish_manifest(
            manifest,
            response=response,
            fetched_count=fetched,
            parsed_count=len(items),
            status=status,
            errors=errors,
        )
        self.store.write_fetch_manifest(manifest)
        inserted = await self.state.put_items(items)
        return CollectorResult(
            source=self.source,
            items=items,
            manifests=[manifest],
            health=health_from(
                self.source, started, status, fetched, len(items), len(inserted), errors
            ),
        )

    def _entry(self, entry: Any, blob: str, now: datetime) -> SourceItem | None:
        url = str(entry.get("link") or entry.get("id") or "")
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d+\.\d+)(?:v(\d+))?", url)
        if not match:
            return None
        paper_id = match.group(1)
        explicit_version = match.group(2) or entry.get("arxiv_version")
        version = int(explicit_version or 1)
        occurred = parse_datetime(entry.get("published") or entry.get("updated")) or now
        updated = parse_datetime(entry.get("updated"))
        authors = [author.get("name", "") for author in entry.get("authors", [])]
        categories = [tag.get("term", "") for tag in entry.get("tags", [])]
        announce_type = str(
            entry.get("arxiv_announce_type") or entry.get("announce_type") or "new"
        ).lower()
        if announce_type in {"replace", "withdraw"} and not explicit_version:
            revision = (updated or occurred).strftime("%Y%m%dT%H%M%S")
            item_id = f"arxiv:{paper_id}:{announce_type}:{revision}"
        elif announce_type in {"cross", "cross-list"}:
            revision = (updated or occurred).strftime("%Y%m%d")
            item_id = f"arxiv:{paper_id}:cross:{revision}"
        else:
            item_id = f"arxiv:{paper_id}:v{version}"
        return SourceItem(
            item_id=item_id,
            item_type="paper",
            source=self.source,
            surface="category_feed",
            change=(
                "withdrawn"
                if announce_type == "withdraw"
                else "version"
                if announce_type == "replace" or version > 1
                else "cross_listed"
                if announce_type in {"cross", "cross-list"}
                else "announced"
            ),
            occurred_at=occurred,
            updated_at=updated,
            first_observed_at=now,
            handoff_at=updated or occurred,
            time_basis=(
                TimeBasis.UPDATED
                if updated and (version > 1 or announce_type in {"replace", "withdraw"})
                else TimeBasis.OCCURRED
            ),
            content_status=(
                ContentStatus.TOMBSTONE
                if announce_type == "withdraw"
                else ContentStatus.FULL
            ),
            raw_refs=[blob],
            payload={
                "arxiv_id": paper_id,
                "version": version,
                "title": " ".join(str(entry.get("title", "")).split()),
                "abstract": " ".join(str(entry.get("summary", "")).split()),
                "authors": authors,
                "categories": categories,
                "announce_type": announce_type,
                "doi": entry.get("arxiv_doi"),
                "journal_ref": entry.get("arxiv_journal_reference"),
                "url": f"https://arxiv.org/abs/{paper_id}",
                "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
            },
        )
