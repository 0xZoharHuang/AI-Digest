from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from ..store import x_expiry
from .base import (
    Collector,
    SafeHTTPClient,
    fetch_external_metadata,
    finish_manifest,
    health_from,
    new_fetch_manifest,
)


class XForYouCollector(Collector):
    source = "x_for_you"

    async def interactive_login(self) -> None:
        profile = Path(str(self.config.get("profile_dir", ""))).expanduser()
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(profile), headless=False, viewport={"width": 1280, "height": 900}
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://x.com/login", wait_until="domcontentloaded")
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=300_000)
            await context.close()

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        profile = Path(str(self.config.get("profile_dir", ""))).expanduser()
        extracted: list[dict[str, Any]] = []
        errors: list[str] = []
        manifest = new_fetch_manifest(self.source, "https://x.com/home")
        try:
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    str(profile), headless=True, viewport={"width": 1280, "height": 900}
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
                seen: set[str] = set()
                for _ in range(int(self.config.get("scroll_rounds", 30))):
                    rows = await page.evaluate(
                        """
                        () => [...document.querySelectorAll('article[data-testid="tweet"]')].map(el => {
                          const time = el.querySelector('time');
                          const status = time?.closest('a')?.getAttribute('href') || '';
                          const text = el.querySelector('[data-testid="tweetText"]')?.innerText || '';
                          const user = el.querySelector('[data-testid="User-Name"]')?.innerText || '';
                          const quote = [...el.querySelectorAll('[role="link"]')]
                            .map(x => x.innerText || '').find(x => x.length > text.length && x.length < 2000) || '';
                          const links = [...el.querySelectorAll('a[href]')].map(a => a.href)
                            .filter(h => h && !h.includes('/compose/') && !h.includes('/analytics'));
                          return {status, text, user, quote, links, datetime: time?.dateTime || ''};
                        })
                        """
                    )
                    for row in rows:
                        post_id = _post_id(row.get("status", ""))
                        if post_id and post_id not in seen:
                            row["post_id"] = post_id
                            extracted.append(row)
                            seen.add(post_id)
                    if len(extracted) >= int(self.config.get("limit", 150)):
                        break
                    await page.mouse.wheel(0, 1400)
                    await page.wait_for_timeout(700)
                await context.close()
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

        blob_ref = self.store.write_blob(
            __import__("json").dumps(extracted, ensure_ascii=False), ".json"
        )
        await self._enrich_links(extracted)
        items = [self._to_item(row, blob_ref, now) for row in extracted]
        inserted = await self.state.put_items(items)
        for item in items:
            self.store.write_revision(item)
        status = HealthStatus.SUCCESS if items and not errors else HealthStatus.PARTIAL
        if not items:
            status = HealthStatus.FAILED
            if not errors:
                errors.append("selector returned zero posts")
        manifest.blob_refs = [blob_ref]
        manifest = finish_manifest(
            manifest,
            fetched_count=len(extracted),
            parsed_count=len(items),
            status=status,
            errors=errors,
        )
        self.store.write_fetch_manifest(manifest)
        return CollectorResult(
            source=self.source,
            items=items,
            manifests=[manifest],
            health=health_from(
                self.source, started, status, len(extracted), len(items), len(inserted), errors
            ),
        )

    def _to_item(self, row: dict[str, Any], blob_ref: str, now: datetime) -> SourceItem:
        occurred = None
        if row.get("datetime"):
            occurred = datetime.fromisoformat(str(row["datetime"]).replace("Z", "+00:00"))
        post_id = str(row["post_id"])
        return SourceItem(
            item_id=f"x_for_you:{post_id}",
            item_type="x_post",
            source=self.source,
            surface="for_you",
            occurred_at=occurred,
            first_observed_at=now,
            handoff_at=now,
            time_basis=TimeBasis.OBSERVED,
            content_status=ContentStatus.FULL,
            raw_refs=[blob_ref],
            expires_at=x_expiry(now, int(self.config.get("retention_days", 30))),
            payload={
                "post_id": post_id,
                "text": row.get("text", ""),
                "author_display": row.get("user", ""),
                "quoted_text": row.get("quote", ""),
                "links": row.get("links", []),
                "link_metadata": row.get("link_metadata", []),
                "url": f"https://x.com{row.get('status', f'/i/status/{post_id}')}",
            },
        )

    async def _enrich_links(self, rows: list[dict[str, Any]]) -> None:
        client = SafeHTTPClient(timeout=12, max_attempts=2)
        cache: dict[str, dict[str, Any]] = {}
        remaining = int(self.config.get("external_metadata_limit", 30))
        try:
            for row in rows:
                metadata = []
                for url in row.get("links", []):
                    host = urlparse(url).hostname
                    if not host or host.endswith("x.com") or remaining <= 0:
                        continue
                    if url not in cache:
                        try:
                            cache[url] = await fetch_external_metadata(client, url)
                        except Exception as error:
                            cache[url] = {"requested_url": url, "error": str(error)[:300]}
                        remaining -= 1
                    metadata.append(cache[url])
                row["link_metadata"] = metadata
        finally:
            await client.close()


def _post_id(path: str) -> str | None:
    marker = "/status/"
    if marker not in path:
        return None
    value = path.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0]
    return value if value.isdigit() else None
