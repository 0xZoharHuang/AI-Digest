from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from ..utils import parse_datetime
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class HuggingFaceCollector(Collector):
    source = "huggingface"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        url = str(self.config.get("url", "https://huggingface.co/api/papers"))
        client = SafeHTTPClient(timeout=40)
        manifest = new_fetch_manifest(self.source, url)
        errors: list[str] = []
        response = None
        items: list[SourceItem] = []
        rows: list[dict[str, Any]] = []
        try:
            response = await client.request(
                "GET", url, params={"limit": int(self.config.get("limit", 100))}
            )
            blob = self.store.write_blob(response.text, ".json")
            rows = response.json()
            for row in rows:
                paper_id = str(row.get("id", ""))
                if not paper_id:
                    continue
                published = parse_datetime(row.get("publishedAt"))
                items.append(
                    SourceItem(
                        item_id=f"huggingface:daily:{paper_id}",
                        item_type="hf_daily_paper",
                        source=self.source,
                        surface="daily_papers",
                        change="entered_surface",
                        occurred_at=published,
                        first_observed_at=now,
                        handoff_at=now,
                        time_basis=TimeBasis.OBSERVED,
                        content_status=ContentStatus.FULL,
                        raw_refs=[blob],
                        payload={
                            "arxiv_id": paper_id,
                            "title": row.get("title", ""),
                            "summary": row.get("summary", ""),
                            "hf_ai_summary": row.get("ai_summary", ""),
                            "upvotes": row.get("upvotes", 0),
                            "published_at": row.get("publishedAt"),
                            "authors": row.get("authors") or [],
                            "github_repo": row.get("githubRepo"),
                            "organization": row.get("organization"),
                            "project_page": row.get("projectPage"),
                            "url": f"https://huggingface.co/papers/{paper_id}",
                        },
                    )
                )
            manifest.blob_refs = [blob]
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            await client.close()
        for item in items:
            self.store.write_revision(item)
        status = HealthStatus.SUCCESS if not errors else HealthStatus.FAILED
        manifest = finish_manifest(
            manifest,
            response=response,
            fetched_count=len(rows),
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
                self.source, started, status, len(rows), len(items), len(inserted), errors
            ),
        )
