from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..models import (
    CollectorResult,
    ContentStatus,
    HealthStatus,
    SourceItem,
    TimeBasis,
)
from ..store import x_expiry
from ..x_auth import XTokenStore
from .base import (
    Collector,
    SafeHTTPClient,
    fetch_external_metadata,
    finish_manifest,
    health_from,
    new_fetch_manifest,
)


class XListCollector(Collector):
    source = "x_list"

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        token_store = XTokenStore()
        tokens = token_store.load()
        token = tokens.access_token if tokens else ""
        list_id = str(self.config.get("list_id", ""))
        if not token or not list_id:
            error = "AI_DIGEST_X_ACCESS_TOKEN and x_list.list_id are required"
            return CollectorResult(
                source=self.source,
                health=health_from(self.source, started, HealthStatus.FAILED, 0, 0, 0, [error]),
            )

        url = f"https://api.x.com/2/lists/{list_id}/tweets"
        client = SafeHTTPClient(timeout=30)
        manifests = []
        items: list[SourceItem] = []
        errors: list[str] = []
        pagination_token: str | None = None
        fetched = 0
        pages = 0
        stopped_at_known = False
        metadata_remaining = int(self.config.get("external_metadata_limit", 30))
        try:
            while pages < int(self.config.get("max_pages", 8)):
                pages += 1
                manifest = new_fetch_manifest(self.source, url)
                params: dict[str, Any] = {
                    "max_results": 100,
                    "tweet.fields": (
                        "id,text,author_id,created_at,conversation_id,edit_history_tweet_ids,"
                        "entities,public_metrics,referenced_tweets"
                    ),
                    "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
                    "user.fields": "id,name,username,verified",
                }
                if pagination_token:
                    params["pagination_token"] = pagination_token
                try:
                    response = await client.request(
                        "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params
                    )
                except Exception as error:
                    status = getattr(getattr(error, "response", None), "status_code", None)
                    if status != 401 or not tokens or not tokens.refresh_token:
                        raise
                    tokens = await token_store.refresh(tokens.refresh_token)
                    token = tokens.access_token
                    response = await client.request(
                        "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params
                    )
                payload = response.json()
                data = payload.get("data") or []
                includes = payload.get("includes") or {}
                users = {user["id"]: user for user in includes.get("users", [])}
                referenced = {tweet["id"]: tweet for tweet in includes.get("tweets", [])}
                fetched += len(data)
                fresh = []
                for tweet in data:
                    if await self.state.has_item(f"x_list:{tweet['id']}"):
                        stopped_at_known = True
                        break
                    fresh.append(tweet)
                page_items = []
                for tweet in fresh:
                    author = users.get(str(tweet.get("author_id")), {})
                    references = [
                        {
                            **reference,
                            "expanded": referenced.get(str(reference.get("id")), {}),
                        }
                        for reference in tweet.get("referenced_tweets") or []
                    ]
                    blob_ref = self.store.write_blob(
                        json.dumps(
                            {"post": tweet, "author": author, "references": references},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        ".x-post.json",
                    )
                    page_items.append(
                        self._to_item(tweet, users, referenced, blob_ref, now)
                    )
                metadata_remaining = await self._enrich_links(
                    client, page_items, metadata_remaining
                )
                items.extend(page_items)
                api_errors = payload.get("errors") or []
                if api_errors:
                    errors.extend(str(error)[:500] for error in api_errors)
                manifest.blob_refs = [ref for item in page_items for ref in item.raw_refs]
                manifests.append(
                    finish_manifest(
                        manifest,
                        response=response,
                        fetched_count=len(data),
                        parsed_count=len(page_items),
                        status=HealthStatus.PARTIAL if api_errors else HealthStatus.SUCCESS,
                        errors=[str(error)[:500] for error in api_errors],
                    )
                )
                pagination_token = (payload.get("meta") or {}).get("next_token")
                if stopped_at_known or not pagination_token:
                    break
            if pagination_token and not stopped_at_known:
                errors.append(
                    f"pagination_exhausted: next_token remained after {pages} page(s)"
                )
        except Exception as error:  # source failure is isolated by orchestrator
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            await client.close()

        for item in items:
            self.store.write_revision(item)
        for manifest in manifests:
            self.store.write_fetch_manifest(manifest)
        inserted = await self.state.put_items(items)
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

    def _to_item(
        self,
        tweet: dict[str, Any],
        users: dict[str, dict[str, Any]],
        referenced: dict[str, dict[str, Any]],
        blob_ref: str,
        observed_at: datetime,
    ) -> SourceItem:
        created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
        author = users.get(str(tweet.get("author_id")), {})
        reference_rows = []
        for reference in tweet.get("referenced_tweets") or []:
            target = referenced.get(str(reference.get("id")), {})
            reference_rows.append(
                {
                    "type": reference.get("type"),
                    "id": reference.get("id"),
                    "text": target.get("text"),
                }
            )
        metrics = tweet.get("public_metrics") or {}
        return SourceItem(
            item_id=f"x_list:{tweet['id']}",
            item_type="x_post",
            source=self.source,
            surface="private_list",
            occurred_at=created,
            first_observed_at=observed_at,
            handoff_at=created,
            time_basis=TimeBasis.OCCURRED,
            content_status=ContentStatus.FULL,
            raw_refs=[blob_ref],
            expires_at=x_expiry(observed_at, int(self.config.get("retention_days", 30))),
            payload={
                "post_id": tweet["id"],
                "text": tweet.get("text", ""),
                "author_id": tweet.get("author_id"),
                "author": author,
                "conversation_id": tweet.get("conversation_id"),
                "edit_history_post_ids": tweet.get("edit_history_tweet_ids") or [],
                "entities": tweet.get("entities") or {},
                "link_metadata": [],
                "references": reference_rows,
                "metrics": metrics,
                "url": f"https://x.com/{author.get('username', 'i')}/status/{tweet['id']}",
            },
        )

    async def _enrich_links(
        self, client: SafeHTTPClient, items: list[SourceItem], remaining: int
    ) -> int:
        cache: dict[str, dict[str, Any]] = {}
        for item in items:
            metadata = []
            urls = (item.payload.get("entities") or {}).get("urls") or []
            for row in urls:
                url = row.get("expanded_url") or row.get("unwound_url")
                host = urlparse(str(url)).hostname if url else None
                if not url or not host or host.endswith("x.com") or remaining <= 0:
                    continue
                if url not in cache:
                    try:
                        cache[url] = await fetch_external_metadata(client, str(url))
                    except Exception as error:
                        cache[url] = {"requested_url": url, "error": str(error)[:300]}
                    remaining -= 1
                metadata.append(cache[url])
            item.payload["link_metadata"] = metadata
        return remaining
