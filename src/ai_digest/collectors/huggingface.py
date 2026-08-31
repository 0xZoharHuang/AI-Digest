from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any

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


class HuggingFaceCollector(Collector):
    source = "huggingface"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        url = str(self.config.get("url", "https://huggingface.co/api/daily_papers"))
        client = SafeHTTPClient(timeout=40)
        errors: list[str] = []
        items: list[SourceItem] = []
        manifests = []
        fetched = 0
        completed_dates: list[date] = []
        cursor_key = "huggingface:last_success_date"
        previous_text = await self.state.get_cursor(cursor_key)
        target_date = now.date()
        requested_dates = dates_to_fetch(
            previous_text,
            target_date,
            max(1, int(self.config.get("max_backfill_dates_per_poll", 3))),
        )
        try:
            limit = max(1, int(self.config.get("page_size", self.config.get("limit", 100))))
            max_pages = max(1, int(self.config.get("max_pages", 50)))
            for requested_date in requested_dates:
                manifest = new_fetch_manifest(self.source, url, now)
                response = None
                rows: list[dict[str, Any]] = []
                blob_refs: list[str] = []
                date_errors: list[str] = []
                manifest.cursor_before = (
                    completed_dates[-1].isoformat() if completed_dates else previous_text
                )
                try:
                    pagination_complete = False
                    for page in range(max_pages):
                        response = await client.request(
                            "GET",
                            url,
                            params={
                                "date": requested_date.isoformat(),
                                "limit": limit,
                                "p": page,
                            },
                        )
                        blob_refs.append(self.store.write_blob(response.text, ".json"))
                        page_rows = response.json()
                        if not isinstance(page_rows, list):
                            raise RuntimeError(
                                "Hugging Face daily papers returned a non-list payload"
                            )
                        fetched += len(page_rows)
                        rows.extend(page_rows)
                        if len(page_rows) < limit:
                            pagination_complete = True
                            break
                    if not pagination_complete:
                        raise RuntimeError(
                            f"Hugging Face pagination reached max_pages={max_pages}"
                        )
                    items.extend(
                        item
                        for raw_row in rows
                        if (
                            item := self._item(
                                raw_row,
                                blob_refs,
                                now,
                                requested_date,
                            )
                        )
                        is not None
                    )
                    completed_dates.append(requested_date)
                    manifest.cursor_after = requested_date.isoformat()
                except Exception as error:
                    message = f"{type(error).__name__}: {error}"
                    errors.append(f"{requested_date.isoformat()}: {message}")
                    date_errors.append(message)
                manifest.request.update(
                    {"date": requested_date.isoformat(), "page_size": limit}
                )
                manifest.blob_refs = blob_refs
                manifest = finish_manifest(
                    manifest,
                    response=response,
                    fetched_count=len(rows),
                    parsed_count=sum(
                        1
                        for item in items
                        if item.payload.get("requested_date") == requested_date.isoformat()
                    ),
                    status=HealthStatus.SUCCESS if not date_errors else HealthStatus.FAILED,
                    errors=date_errors,
                )
                self.store.write_fetch_manifest(manifest)
                manifests.append(manifest)
                if date_errors:
                    break
        finally:
            await client.close()
        for item in items:
            self.store.write_revision(item)
        cursor_after = completed_dates[-1] if completed_dates else _parse_date(previous_text)
        backlog_remaining = max(
            0,
            (target_date - cursor_after).days if cursor_after is not None else 0,
        )
        status = (
            HealthStatus.FAILED
            if errors and not completed_dates
            else HealthStatus.PARTIAL
            if errors or backlog_remaining
            else HealthStatus.SUCCESS
        )
        inserted = await self.state.put_items(items)
        if completed_dates:
            await self.state.set_cursor(cursor_key, completed_dates[-1].isoformat())
        result = CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health_from(
                self.source, started, status, fetched, len(items), len(inserted), errors
            ),
        )
        result.health.surfaces = {
            "dated_pages": {
                "cursor_before": previous_text,
                "cursor_after": cursor_after.isoformat() if cursor_after else previous_text,
                "requested_dates": [value.isoformat() for value in requested_dates],
                "completed_dates": [value.isoformat() for value in completed_dates],
                "backlog_days": backlog_remaining,
            }
        }
        result.health.raw_receipts_complete = not errors
        if backlog_remaining:
            result.health.warnings.append(
                f"Hugging Face dated backlog remains: {backlog_remaining} day(s)"
            )
        if status == HealthStatus.SUCCESS and not items:
            result.health.quiet_reason = (
                "dated Hugging Face Daily Papers pages were already current or empty"
            )
        return result

    def _item(
        self,
        raw_row: dict[str, Any],
        blob_refs: list[str],
        now: datetime,
        requested_date: date,
    ) -> SourceItem | None:
        row = raw_row.get("paper", raw_row)
        if not isinstance(row, dict):
            return None
        paper_id = str(row.get("id", ""))
        if not paper_id:
            return None
        published = parse_datetime(row.get("publishedAt"))
        content_hash = sha256_text(
            json_dumps(
                {
                    "title": row.get("title", ""),
                    "summary": row.get("summary", ""),
                    "ai_summary": row.get("ai_summary", ""),
                    "github_repo": row.get("githubRepo"),
                }
            )
        )
        return SourceItem(
            item_id=f"huggingface:daily:{paper_id}",
            item_type="hf_daily_paper",
            source=self.source,
            surface="daily_papers",
            change="entered_surface",
            occurred_at=published,
            first_observed_at=now,
            handoff_at=now,
            ready_at=now,
            entity_key=f"arxiv:{paper_id}",
            content_hash=content_hash,
            observation_kind=(
                ObservationKind.LATE_ARRIVAL
                if requested_date < now.date()
                else ObservationKind.LIVE_INCREMENT
            ),
            time_basis=TimeBasis.OBSERVED,
            content_status=ContentStatus.FULL,
            raw_refs=list(blob_refs),
            payload={
                "arxiv_id": paper_id,
                "title": row.get("title", ""),
                "summary": row.get("summary", ""),
                "hf_ai_summary": row.get("ai_summary", ""),
                "upvotes": raw_row.get("upvotes", row.get("upvotes", 0)),
                "published_at": row.get("publishedAt"),
                "authors": row.get("authors") or [],
                "github_repo": row.get("githubRepo"),
                "organization": row.get("organization"),
                "project_page": row.get("projectPage"),
                "requested_date": requested_date.isoformat(),
                "url": f"https://huggingface.co/papers/{paper_id}",
            },
        )


def dates_to_fetch(previous_text: str | None, target: date, limit: int) -> list[date]:
    previous = _parse_date(previous_text)
    if previous is None:
        return [target]
    if previous >= target:
        return []
    start = previous + timedelta(days=1)
    count = min(max(1, limit), (target - previous).days)
    return [start + timedelta(days=offset) for offset in range(count)]


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
