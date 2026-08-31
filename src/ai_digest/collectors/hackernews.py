from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import (
    CollectorResult,
    ContentStatus,
    HealthStatus,
    ObservationKind,
    SourceItem,
    TimeBasis,
)
from ..utils import json_dumps, sha256_text
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class HackerNewsCollector(Collector):
    """Incrementally scan HN item ids and attach current attention surfaces."""

    source = "hackernews"
    base = "https://hacker-news.firebaseio.com/v0"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        now = now.astimezone(UTC)
        client = SafeHTTPClient(timeout=25)
        manifests = []
        errors: list[str] = []
        fetched = 0
        items: list[SourceItem] = []
        cursor_key = "hackernews:maxitem"
        previous_text = await self.state.get_cursor(cursor_key)
        previous = int(previous_text) if previous_text and previous_text.isdigit() else None
        max_item: int | None = None

        try:
            max_url = f"{self.base}/maxitem.json"
            max_manifest = new_fetch_manifest(self.source, max_url, now)
            max_response = await client.request("GET", max_url)
            max_item = int(max_response.json())
            max_blob = self.store.write_blob(max_response.text, ".json")
            max_manifest.blob_refs = [max_blob]
            max_manifest.cursor_before = previous_text
            max_manifest.cursor_after = str(max_item)
            manifests.append(
                finish_manifest(max_manifest, response=max_response, fetched_count=1, parsed_count=1)
            )

            surface_ids: dict[str, list[int]] = {}
            for surface in ("new", "top", "show"):
                url = f"{self.base}/{surface}stories.json"
                manifest = new_fetch_manifest(f"hackernews:{surface}", url, now)
                response = await client.request("GET", url)
                blob = self.store.write_blob(response.text, ".json")
                limit = int(self.config.get(f"{surface}_limit", 500))
                values = [int(value) for value in response.json()[:limit]]
                surface_ids[surface] = values
                manifest.blob_refs = [blob]
                manifests.append(
                    finish_manifest(
                        manifest,
                        response=response,
                        fetched_count=len(values),
                        parsed_count=len(values),
                    )
                )

            if previous is None:
                scan_ids = list(surface_ids["new"])
            else:
                gap = max(0, max_item - previous)
                max_scan = int(self.config.get("max_incremental_ids", 20_000))
                if gap > max_scan:
                    raise RuntimeError(
                        f"HN incremental gap {gap} exceeds safe cap {max_scan}; cursor retained"
                    )
                scan_ids = list(range(previous + 1, max_item + 1))

            surfaces_by_id: dict[int, list[str]] = {}
            for surface, values in surface_ids.items():
                for item_id in values:
                    surfaces_by_id.setdefault(item_id, []).append(surface)
            # Surface lists are attention metadata, not separate observation streams. Fetch only
            # the durable incremental ids; otherwise every poll would re-fetch ~1,000 known stories.
            candidate_ids = list(dict.fromkeys(scan_ids))
            fetched += len(candidate_ids)
            semaphore = asyncio.Semaphore(int(self.config.get("item_concurrency", 20)))

            async def load(item_id: int) -> tuple[int, dict[str, Any] | None, str | None]:
                async with semaphore:
                    try:
                        response = await client.request("GET", f"{self.base}/item/{item_id}.json")
                        return item_id, response.json(), self.store.write_blob(response.text, ".json")
                    except Exception as error:
                        errors.append(f"item {item_id}: {type(error).__name__}: {error}")
                        return item_id, None, None

            rows = await asyncio.gather(*(load(item_id) for item_id in candidate_ids))
            for item_id, story, item_blob in rows:
                if not story or story.get("type") != "story" or not item_blob:
                    continue
                occurred = datetime.fromtimestamp(int(story.get("time", 0)), UTC)
                stable = {
                    "title": story.get("title", ""),
                    "url": story.get("url"),
                    "text": story.get("text", ""),
                }
                items.append(
                    SourceItem(
                        item_id=f"hackernews:{item_id}",
                        item_type="hn_story",
                        source=self.source,
                        surface="incremental",
                        change="first_seen",
                        occurred_at=occurred,
                        first_observed_at=now,
                        handoff_at=occurred,
                        ready_at=now,
                        observation_kind=(
                            ObservationKind.BOOTSTRAP_SNAPSHOT
                            if previous is None and occurred < now - timedelta(hours=24)
                            else ObservationKind.LIVE_INCREMENT
                        ),
                        entity_key=f"hackernews:{item_id}",
                        content_hash=sha256_text(json_dumps(stable)),
                        time_basis=TimeBasis.OCCURRED,
                        content_status=(
                            ContentStatus.TOMBSTONE
                            if story.get("deleted") or story.get("dead")
                            else ContentStatus.FULL
                        ),
                        raw_refs=[item_blob],
                        payload={
                            "story_id": item_id,
                            **stable,
                            "by": story.get("by"),
                            "score": story.get("score", 0),
                            "comments": story.get("descendants", 0),
                            "surfaces": sorted(surfaces_by_id.get(item_id, [])),
                            "hn_url": f"https://news.ycombinator.com/item?id={item_id}",
                        },
                    )
                )
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            await client.close()

        for item in items:
            self.store.write_revision(item)
        for manifest in manifests:
            self.store.write_fetch_manifest(manifest)
        inserted = await self.state.put_items(items)
        complete = max_item is not None and not errors
        if complete:
            await self.state.set_cursor(cursor_key, str(max_item))
        status = (
            HealthStatus.SUCCESS
            if complete
            else HealthStatus.PARTIAL
            if items
            else HealthStatus.FAILED
        )
        result = CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health_from(
                self.source,
                started,
                status,
                fetched,
                len(items),
                len(inserted),
                errors,
            ),
        )
        result.health.surfaces = {
            "incremental": {
                "cursor_before": previous,
                "cursor_after": max_item if complete else previous,
                "candidate_count": fetched,
            }
        }
        result.health.raw_receipts_complete = complete
        if complete and not items:
            result.health.quiet_reason = "maxitem cursor confirmed no new HN items"
        return result
