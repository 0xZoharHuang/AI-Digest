from __future__ import annotations

import asyncio
import calendar
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import feedparser

from ..models import (
    CollectorResult,
    ContentStatus,
    HealthStatus,
    ObservationKind,
    SourceItem,
    TimeBasis,
)
from ..utils import json_dumps, parse_datetime, sha256_text
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
        manifests = []
        errors: list[str] = []
        rss_errors: list[str] = []
        items: list[SourceItem] = []
        fetched = 0
        rss_fetched = 0
        rss_parsed = 0
        feed_metadata: dict[str, Any] = {}
        response = None
        rss_success = False
        cursor_key = "arxiv:last_submission_backfill_date"
        previous_text = await self.state.get_cursor(cursor_key)
        previous_date = _parse_date(previous_text)
        backfill_dates = dates_to_backfill(
            previous_text,
            now.date(),
            max(1, int(self.config.get("max_backfill_dates_per_poll", 3))),
        )
        completed_backfill_dates: list[date] = []
        try:
            response = await client.request("GET", url)
            blob = self.store.write_blob(response.text, ".xml")
            parsed = feedparser.parse(response.content)
            feed_metadata = {
                "title": parsed.feed.get("title"),
                "updated": parsed.feed.get("updated"),
                "published": parsed.feed.get("published"),
            }
            entries = list(parsed.entries)
            if parsed.bozo:
                message = f"feed_parse: {parsed.bozo_exception}"
                errors.append(message)
                rss_errors.append(message)
            rss_fetched = len(entries)
            fetched = rss_fetched
            for entry in entries:
                item = self._entry(entry, blob, now)
                if item:
                    items.append(item)
                    rss_parsed += 1
            manifest.blob_refs = [blob]
            rss_success = not parsed.bozo

            api_url = str(
                self.config.get("api_url", "https://export.arxiv.org/api/query")
            )
            page_size = max(1, min(500, int(self.config.get("api_page_size", 500))))
            max_pages = max(1, int(self.config.get("api_max_pages", 4)))
            categories_config = [str(value) for value in self.config.get("categories", [])]
            category_query = " OR ".join(f"cat:{value}" for value in categories_config)
            api_request_count = 0
            for backfill_date in backfill_dates:
                api_manifest = new_fetch_manifest(f"{self.source}:api_backfill", api_url, now)
                api_manifest.cursor_before = (
                    completed_backfill_dates[-1].isoformat()
                    if completed_backfill_dates
                    else previous_text
                )
                date_items: list[SourceItem] = []
                date_fetched = 0
                blob_refs: list[str] = []
                api_response = None
                date_errors: list[str] = []
                try:
                    date_text = backfill_date.strftime("%Y%m%d")
                    query = (
                        f"({category_query}) AND "
                        f"submittedDate:[{date_text}0000 TO {date_text}2359]"
                    )
                    pagination_complete = False
                    for page in range(max_pages):
                        if api_request_count:
                            await asyncio.sleep(3)
                        api_request_count += 1
                        api_response = await client.request(
                            "GET",
                            api_url,
                            params={
                                "search_query": query,
                                "start": page * page_size,
                                "max_results": page_size,
                                "sortBy": "submittedDate",
                                "sortOrder": "ascending",
                            },
                        )
                        blob = self.store.write_blob(api_response.text, ".xml")
                        blob_refs.append(blob)
                        api_feed = feedparser.parse(api_response.content)
                        if api_feed.bozo:
                            raise RuntimeError(f"API feed parse: {api_feed.bozo_exception}")
                        entries = list(api_feed.entries)
                        date_fetched += len(entries)
                        fetched += len(entries)
                        for entry in entries:
                            item = self._entry(entry, blob, now)
                            if item is not None:
                                payload = dict(item.payload)
                                payload["backfill_date"] = backfill_date.isoformat()
                                date_items.append(
                                    item.model_copy(
                                        update={
                                            "surface": "api_backfill",
                                            "observation_kind": ObservationKind.LATE_ARRIVAL,
                                            "payload": payload,
                                        }
                                    )
                                )
                        total_results = int(
                            api_feed.feed.get("opensearch_totalresults", len(entries))
                            or 0
                        )
                        if page * page_size + len(entries) >= total_results:
                            pagination_complete = True
                            break
                    if not pagination_complete:
                        raise RuntimeError(
                            f"arXiv API pagination reached api_max_pages={max_pages}"
                        )
                    api_manifest.cursor_after = backfill_date.isoformat()
                except Exception as error:
                    message = f"{type(error).__name__}: {error}"
                    errors.append(f"backfill {backfill_date.isoformat()}: {message}")
                    date_errors.append(message)
                api_manifest.request.update(
                    {
                        "date": backfill_date.isoformat(),
                        "categories": categories_config,
                        "page_size": page_size,
                    }
                )
                api_manifest.blob_refs = blob_refs
                api_manifest = finish_manifest(
                    api_manifest,
                    response=api_response,
                    fetched_count=date_fetched,
                    parsed_count=len(date_items),
                    status=HealthStatus.SUCCESS if not date_errors else HealthStatus.FAILED,
                    errors=date_errors,
                )
                self.store.write_fetch_manifest(api_manifest)
                manifests.append(api_manifest)
                if not date_errors:
                    items.extend(date_items)
                    completed_backfill_dates.append(backfill_date)
                if date_errors:
                    break
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            errors.append(message)
            rss_errors.append(message)
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
            fetched_count=rss_fetched,
            parsed_count=rss_parsed,
            status=(
                HealthStatus.SUCCESS
                if not rss_errors
                else HealthStatus.PARTIAL
                if rss_parsed
                else HealthStatus.FAILED
            ),
            errors=rss_errors,
        )
        self.store.write_fetch_manifest(manifest)
        manifests.insert(0, manifest)
        inserted = await self.state.put_items(items)
        cursor_after = completed_backfill_dates[-1] if completed_backfill_dates else previous_date
        if rss_success and (cursor_after is None or cursor_after >= now.date() - timedelta(days=1)):
            cursor_after = now.date()
        if cursor_after is not None:
            await self.state.set_cursor(cursor_key, cursor_after.isoformat())
        backlog_days = max(
            0,
            (now.date() - cursor_after).days if cursor_after is not None else 0,
        )
        if backlog_days and status == HealthStatus.SUCCESS:
            status = HealthStatus.PARTIAL
        result = CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health_from(
                self.source, started, status, fetched, len(items), len(inserted), errors
            ),
        )
        result.health.status = status
        result.health.raw_receipts_complete = not errors
        result.health.surfaces = {
            "category_feed": feed_metadata,
            "submission_date_backfill": {
                "cursor_before": previous_text,
                "cursor_after": cursor_after.isoformat() if cursor_after else previous_text,
                "requested_dates": [value.isoformat() for value in backfill_dates],
                "completed_dates": [
                    value.isoformat() for value in completed_backfill_dates
                ],
                "backlog_days": backlog_days,
                "coverage_note": (
                    "bounded supplement for newly submitted papers; not a replay of RSS "
                    "announcement, replacement, withdrawal, or cross-list history"
                ),
            },
        }
        if backlog_days:
            result.health.warnings.append(
                f"arXiv submittedDate supplement backlog remains: {backlog_days} day(s)"
            )
        if backfill_dates:
            result.health.warnings.append(
                "arXiv API submittedDate backfill is bounded discovery, not exact RSS announcement replay"
            )
        if status == HealthStatus.SUCCESS and not items:
            result.health.quiet_reason = (
                "arXiv returned a valid empty daily announcement feed; weekends are expected"
            )
        return result

    def _entry(self, entry: Any, blob: str, now: datetime) -> SourceItem | None:
        url = str(entry.get("link") or entry.get("id") or "")
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d+\.\d+)(?:v(\d+))?", url)
        if not match:
            return None
        paper_id = match.group(1)
        explicit_version = match.group(2) or entry.get("arxiv_version")
        version = int(explicit_version or 1)
        occurred = (
            _feed_datetime(entry.get("published_parsed"))
            or parse_datetime(entry.get("published") or entry.get("updated"))
            or now
        )
        updated = _feed_datetime(entry.get("updated_parsed")) or parse_datetime(
            entry.get("updated")
        )
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
            ready_at=now,
            entity_key=f"arxiv:{paper_id}",
            content_hash=sha256_text(
                json_dumps(
                    {
                        "version": version,
                        "announce_type": announce_type,
                        "title": " ".join(str(entry.get("title", "")).split()),
                        "abstract": " ".join(str(entry.get("summary", "")).split()),
                    }
                )
            ),
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


def _feed_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def dates_to_backfill(previous_text: str | None, target: date, limit: int) -> list[date]:
    previous = _parse_date(previous_text)
    end = target - timedelta(days=1)
    if previous is None or previous >= end:
        return []
    start = previous + timedelta(days=1)
    count = min(max(1, limit), (end - previous).days)
    return [start + timedelta(days=offset) for offset in range(count)]


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
