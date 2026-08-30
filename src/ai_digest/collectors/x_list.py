from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from ..models import (
    CollectorResult,
    ContentStatus,
    FetchManifest,
    HealthStatus,
    SourceItem,
    TimeBasis,
)
from ..store import x_expiry
from ..utils import parse_datetime
from ..x_provider import TwitterApiIOKeyStore
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class XListCollector(Collector):
    """Incrementally read multiple public X Lists through TwitterAPI.io."""

    source = "x_list"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        now = now.astimezone(UTC)
        api_key = TwitterApiIOKeyStore().load()
        list_ids = [str(value) for value in self.config.get("list_ids", []) if str(value)]
        if not api_key or not list_ids:
            missing = "TwitterAPI.io API key" if not api_key else "x_list.list_ids"
            return CollectorResult(
                source=self.source,
                health=health_from(
                    self.source,
                    started,
                    HealthStatus.FAILED,
                    0,
                    0,
                    0,
                    [f"{missing} is required"],
                ),
            )

        base_url = str(
            self.config.get("url", "https://api.twitterapi.io/twitter/list/tweets")
        )
        max_pages = int(self.config.get("max_pages_per_list", 500))
        lookback_hours = int(self.config.get("initial_lookback_hours", 24))
        overlap_seconds = int(self.config.get("cursor_overlap_seconds", 300))
        headers = {"X-API-Key": api_key}
        client = SafeHTTPClient(timeout=45)
        manifests: list[FetchManifest] = []
        merged: dict[str, dict[str, Any]] = {}
        surfaces: dict[str, dict[str, Any]] = {}
        cursor_updates: dict[str, str | None] = {}
        errors: list[str] = []
        fetched_total = 0

        try:
            for list_id in list_ids:
                cursor_key = f"x_list:{list_id}:since_time"
                previous = await self.state.get_cursor(cursor_key)
                since_time = (
                    int(previous)
                    if previous and previous.isdigit()
                    else int((now - timedelta(hours=lookback_hours)).timestamp())
                )
                surface_fetched = 0
                surface_pages = 0
                surface_error: str | None = None
                pagination = ""
                while surface_pages < max_pages:
                    surface_pages += 1
                    manifest = new_fetch_manifest(f"x_list:{list_id}", base_url, now)
                    manifest.cursor_before = str(since_time)
                    params: dict[str, Any] = {
                        "listId": list_id,
                        "sinceTime": since_time,
                        "includeReplies": bool(self.config.get("include_replies", True)),
                    }
                    if pagination:
                        params["cursor"] = pagination
                    try:
                        response = await client.request(
                            "GET",
                            base_url,
                            headers=headers,
                            params=params,
                            data_limit=10_000_000,
                        )
                        payload = response.json()
                        if str(payload.get("status", "success")).lower() == "error":
                            raise RuntimeError(str(payload.get("message") or "provider error"))
                        tweets = payload.get("tweets") or []
                        if not isinstance(tweets, list):
                            raise RuntimeError("TwitterAPI.io returned a non-list tweets payload")
                        surface_fetched += len(tweets)
                        fetched_total += len(tweets)
                        for tweet in tweets:
                            if not isinstance(tweet, dict) or not tweet.get("id"):
                                continue
                            post_id = str(tweet["id"])
                            record = merged.setdefault(post_id, {"post": tweet, "list_ids": []})
                            if list_id not in record["list_ids"]:
                                record["list_ids"].append(list_id)
                        next_cursor = str(payload.get("next_cursor") or "")
                        has_next = bool(
                            payload.get("has_next_page", payload.get("has_more", False))
                        )
                        manifest.cursor_after = next_cursor or None
                        manifests.append(
                            finish_manifest(
                                manifest,
                                response=response,
                                fetched_count=len(tweets),
                                parsed_count=len(tweets),
                            )
                        )
                        if not tweets or not has_next or not next_cursor:
                            break
                        if next_cursor == pagination:
                            raise RuntimeError("TwitterAPI.io returned a repeated cursor")
                        pagination = next_cursor
                    except Exception as error:
                        surface_error = f"{type(error).__name__}: {error}"
                        manifests.append(
                            finish_manifest(
                                manifest,
                                fetched_count=0,
                                parsed_count=0,
                                status=HealthStatus.FAILED,
                                errors=[surface_error],
                            )
                        )
                        break
                else:
                    surface_error = f"page cap reached: {max_pages} pages"

                if surface_error is None:
                    cursor_updates[cursor_key] = str(
                        max(0, int(now.timestamp()) - overlap_seconds)
                    )
                    surface_status = HealthStatus.SUCCESS
                else:
                    errors.append(f"list {list_id}: {surface_error}")
                    surface_status = (
                        HealthStatus.PARTIAL if surface_fetched else HealthStatus.FAILED
                    )
                surfaces[list_id] = {
                    "status": surface_status.value,
                    "fetched_count": surface_fetched,
                    "pages": surface_pages,
                    "since_time": since_time,
                    "error": surface_error,
                }
        finally:
            await client.close()

        items: list[SourceItem] = []
        for record in merged.values():
            tweet = record["post"]
            blob_ref = self.store.write_blob(
                json.dumps(
                    {
                        "provider": "twitterapi_io",
                        "list_ids": sorted(record["list_ids"]),
                        "post": tweet,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                ".x-post.json",
            )
            item = self._to_item(tweet, sorted(record["list_ids"]), blob_ref, now)
            self.store.write_revision(item)
            items.append(item)

        inserted = await self.state.put_items(items)
        await self.state.set_cursors(cursor_updates)
        success_count = sum(
            1 for value in surfaces.values() if value["status"] == HealthStatus.SUCCESS.value
        )
        if success_count == len(list_ids):
            status = HealthStatus.SUCCESS
        elif success_count or fetched_total:
            status = HealthStatus.PARTIAL
        else:
            status = HealthStatus.FAILED
        health = health_from(
            self.source,
            started,
            status,
            fetched_total,
            len(items),
            len(inserted),
            errors,
        )
        health.surfaces = surfaces
        return CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health,
        )

    def _to_item(
        self,
        tweet: dict[str, Any],
        list_ids: list[str],
        blob_ref: str,
        observed_at: datetime,
    ) -> SourceItem:
        post_id = str(tweet["id"])
        created = _x_datetime(tweet.get("createdAt") or tweet.get("created_at")) or observed_at
        author = _mapping(tweet.get("author"))
        entities = _mapping(tweet.get("entities"))
        metrics = {
            "repost_count": tweet.get("retweetCount", tweet.get("retweet_count")),
            "reply_count": tweet.get("replyCount", tweet.get("reply_count")),
            "like_count": tweet.get("likeCount", tweet.get("like_count")),
            "quote_count": tweet.get("quoteCount", tweet.get("quote_count")),
            "view_count": tweet.get("viewCount", tweet.get("view_count")),
            "bookmark_count": tweet.get("bookmarkCount", tweet.get("bookmark_count")),
        }
        username = author.get("userName") or author.get("username")
        return SourceItem(
            item_id=f"x_list:{post_id}",
            item_type="x_post",
            source=self.source,
            surface="public_lists",
            occurred_at=created,
            first_observed_at=observed_at,
            handoff_at=created,
            time_basis=TimeBasis.OCCURRED,
            content_status=ContentStatus.FULL,
            raw_refs=[blob_ref],
            expires_at=x_expiry(observed_at, int(self.config.get("retention_days", 30))),
            payload={
                "provider": "twitterapi_io",
                "post_id": post_id,
                "text": str(tweet.get("text") or ""),
                "author_id": str(author.get("id") or ""),
                "author": {
                    "id": str(author.get("id") or ""),
                    "name": author.get("name"),
                    "username": username,
                    "verified": bool(
                        author.get("isBlueVerified", author.get("verified", False))
                    ),
                },
                "conversation_id": tweet.get("conversationId")
                or tweet.get("conversation_id"),
                "edit_history_post_ids": [post_id],
                "entities": entities,
                "expanded_links": _expanded_links(entities),
                "references": _references(tweet),
                "metrics": {key: value for key, value in metrics.items() if value is not None},
                "list_ids": list_ids,
                "url": str(
                    tweet.get("url") or f"https://x.com/{username or 'i'}/status/{post_id}"
                ),
            },
        )


def _x_datetime(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    try:
        return parsedate_to_datetime(str(value)).astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _references(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, kind in (("quoted_tweet", "quoted"), ("retweeted_tweet", "reposted")):
        value = _mapping(tweet.get(key))
        if not value.get("id"):
            continue
        author = _mapping(value.get("author"))
        rows.append(
            {
                "type": kind,
                "id": str(value["id"]),
                "text": value.get("text"),
                "url": value.get("url"),
                "author": {
                    "id": str(author.get("id") or ""),
                    "username": author.get("userName") or author.get("username"),
                    "name": author.get("name"),
                },
            }
        )
    reply_id = tweet.get("inReplyToId") or tweet.get("in_reply_to_id")
    if reply_id:
        rows.append(
            {
                "type": "replied_to",
                "id": str(reply_id),
                "author_id": tweet.get("inReplyToUserId"),
                "username": tweet.get("inReplyToUsername"),
            }
        )
    return rows


def _expanded_links(entities: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for row in entities.get("urls") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("expanded_url") or row.get("expandedUrl") or row.get("url")
        if value and str(value) not in output:
            output.append(str(value))
    return output


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
