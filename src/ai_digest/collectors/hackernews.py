from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class HackerNewsCollector(Collector):
    source = "hackernews"
    base = "https://hacker-news.firebaseio.com/v0"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        client = SafeHTTPClient(timeout=25)
        manifests = []
        items: list[SourceItem] = []
        errors: list[str] = []
        fetched = 0
        try:
            surfaces: dict[str, list[int]] = {}
            for surface in self.config.get("surfaces", ["new", "top", "show"]):
                url = f"{self.base}/{surface}stories.json"
                manifest = new_fetch_manifest(self.source, url)
                response = await client.request("GET", url)
                blob = self.store.write_blob(response.text, ".json")
                limit = int(self.config.get(f"{surface}_limit", 500))
                ids = [int(value) for value in response.json()[:limit]]
                surfaces[str(surface)] = ids
                fetched += len(ids)
                manifest.blob_refs = [blob]
                manifests.append(
                    finish_manifest(
                        manifest, response=response, fetched_count=len(ids), parsed_count=len(ids)
                    )
                )

            unique_ids = list(dict.fromkeys(value for ids in surfaces.values() for value in ids))
            semaphore = asyncio.Semaphore(20)

            async def load(item_id: int) -> tuple[int, dict[str, Any] | None, str | None]:
                async with semaphore:
                    try:
                        response = await client.request("GET", f"{self.base}/item/{item_id}.json")
                        return (
                            item_id,
                            response.json(),
                            self.store.write_blob(response.text, ".json"),
                        )
                    except Exception:
                        return item_id, None, None

            stories = await asyncio.gather(*(load(item_id) for item_id in unique_ids))
            cutoff = now - timedelta(hours=24)
            by_id = {item_id: (story, blob) for item_id, story, blob in stories}
            for surface, ids in surfaces.items():
                for item_id in ids:
                    story, story_blob = by_id[item_id]
                    if not story or story.get("type") != "story" or not story_blob:
                        continue
                    occurred = datetime.fromtimestamp(int(story.get("time", 0)), UTC)
                    if surface == "new" and occurred < cutoff:
                        continue
                    handoff = occurred if surface == "new" else now
                    basis = TimeBasis.OCCURRED if surface == "new" else TimeBasis.OBSERVED
                    items.append(
                        SourceItem(
                            item_id=f"hackernews:{surface}:{item_id}",
                            item_type="hn_story",
                            source=self.source,
                            surface=surface,
                            change="first_seen" if surface == "new" else "entered_surface",
                            occurred_at=occurred,
                            first_observed_at=now,
                            handoff_at=handoff,
                            time_basis=basis,
                            content_status=(
                                ContentStatus.TOMBSTONE
                                if story.get("deleted") or story.get("dead")
                                else ContentStatus.FULL
                            ),
                            raw_refs=[story_blob],
                            payload={
                                "story_id": item_id,
                                "title": story.get("title", ""),
                                "url": story.get("url"),
                                "text": story.get("text", ""),
                                "by": story.get("by"),
                                "score": story.get("score", 0),
                                "comments": story.get("descendants", 0),
                                "surface": surface,
                                "hn_url": f"https://news.ycombinator.com/item?id={item_id}",
                            },
                        )
                    )
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
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
