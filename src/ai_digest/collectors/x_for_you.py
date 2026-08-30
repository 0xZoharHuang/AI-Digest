from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

from ..config import REPO_ROOT
from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from ..store import x_expiry
from ..utils import atomic_write_json
from .base import (
    Collector,
    SafeHTTPClient,
    fetch_external_metadata,
    finish_manifest,
    health_from,
    new_fetch_manifest,
)


class XForYouCollector(Collector):
    """Personal, cookie-authenticated For You collector migrated from V1."""

    source = "x_for_you"

    def _cookie_path(self) -> Path:
        configured = Path(str(self.config.get("cookie_file", "config/twitter_cookies.json")))
        return configured.expanduser() if configured.is_absolute() else REPO_ROOT / configured

    async def interactive_login(self) -> None:
        cookie_path = self._cookie_path()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            if cookie_path.exists():
                cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
                await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto("https://x.com/home", wait_until="domcontentloaded")
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=300_000)
            await self._select_for_you(page)
            atomic_write_json(cookie_path, await context.cookies())
            await context.close()
            await browser.close()

    async def collect(self, now: datetime) -> CollectorResult:
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        now = now.astimezone(UTC)
        cooldown = await self.state.get_cursor("x_for_you:cooldown_until")
        cooldown_until = datetime.fromisoformat(cooldown) if cooldown else None
        if cooldown_until and cooldown_until > now:
            error = f"account cooldown active until {cooldown_until.isoformat()}"
            return CollectorResult(
                source=self.source,
                health=health_from(
                    self.source, started, HealthStatus.FAILED, 0, 0, 0, [error]
                ),
            )

        cookie_path = self._cookie_path()
        if not cookie_path.exists():
            return CollectorResult(
                source=self.source,
                health=health_from(
                    self.source,
                    started,
                    HealthStatus.FAILED,
                    0,
                    0,
                    0,
                    [f"cookie file is missing: {cookie_path}"],
                ),
            )

        extracted: list[dict[str, Any]] = []
        errors: list[str] = []
        manifest = new_fetch_manifest(self.source, "https://x.com/home", now)
        try:
            cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
                await context.add_cookies(cookies)
                page = await context.new_page()
                await page.goto(
                    "https://x.com/home", wait_until="domcontentloaded", timeout=45_000
                )
                if await self._detect_challenge(page):
                    raise RuntimeError("X challenge or identity verification page detected")
                await page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
                await self._select_for_you(page)
                extracted = await self._scroll_feed(page)
                await context.close()
                await browser.close()
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

        await self._enrich_links(extracted)
        items: list[SourceItem] = []
        for row in extracted:
            blob_ref = self.store.write_blob(
                json.dumps(row, ensure_ascii=False, sort_keys=True), ".x-post.json"
            )
            items.append(self._to_item(row, blob_ref, now))
        for item in items:
            self.store.write_revision(item)
        status = HealthStatus.SUCCESS if items and not errors else HealthStatus.PARTIAL
        if not items:
            status = HealthStatus.FAILED
            if not errors:
                errors.append("For You selector returned zero Posts")
        manifest.blob_refs = list(dict.fromkeys(ref for item in items for ref in item.raw_refs))
        manifest = finish_manifest(
            manifest,
            fetched_count=len(extracted),
            parsed_count=len(items),
            status=status,
            errors=errors,
        )
        self.store.write_fetch_manifest(manifest)
        inserted = await self.state.put_items(items)
        if status == HealthStatus.SUCCESS:
            await self.state.set_cursors(
                {"x_for_you:failures": "0", "x_for_you:cooldown_until": None}
            )
        else:
            await self._record_failure(now)
        return CollectorResult(
            source=self.source,
            items=items,
            manifests=[manifest],
            health=health_from(
                self.source, started, status, len(extracted), len(items), len(inserted), errors
            ),
        )

    async def _select_for_you(self, page: Page) -> None:
        selected = await page.evaluate(
            """
            () => {
              const tabs = [...document.querySelectorAll('a[role="tab"]')];
              const target = tabs.find(tab => (tab.textContent || '').trim() === 'For you');
              if (!target) return 'missing';
              if (target.getAttribute('aria-selected') === 'true') return 'selected';
              target.click();
              return 'clicked';
            }
            """
        )
        if selected == "missing":
            raise RuntimeError("For You tab was not found")
        if selected == "clicked":
            await page.wait_for_timeout(1500)
        verified = await page.evaluate(
            """
            () => [...document.querySelectorAll('a[role="tab"]')].some(tab =>
              (tab.textContent || '').trim() === 'For you' &&
              tab.getAttribute('aria-selected') === 'true')
            """
        )
        if not verified:
            raise RuntimeError("For You tab could not be verified as selected")

    async def _scroll_feed(self, page: Page) -> list[dict[str, Any]]:
        limit = int(self.config.get("limit", 150))
        scroll_rounds = int(self.config.get("scroll_rounds", 45))
        refresh_rounds = int(self.config.get("refresh_rounds", 3))
        consecutive_empty_limit = int(self.config.get("consecutive_empty", 3))
        extracted: list[dict[str, Any]] = []
        seen: set[str] = set()
        for refresh in range(refresh_rounds + 1):
            consecutive_empty = 0
            for _ in range(scroll_rounds):
                rows = await page.evaluate(_EXTRACT_POSTS_JS)
                before = len(extracted)
                for row in rows:
                    post_id = _post_id(str(row.get("status", "")))
                    if not post_id or post_id in seen:
                        continue
                    row["post_id"] = post_id
                    extracted.append(row)
                    seen.add(post_id)
                if len(extracted) >= limit:
                    return extracted[:limit]
                consecutive_empty = consecutive_empty + 1 if len(extracted) == before else 0
                if consecutive_empty >= consecutive_empty_limit:
                    break
                await page.mouse.wheel(0, 1000)
                delay = random.uniform(
                    float(self.config.get("delay_min_seconds", 2.0)),
                    float(self.config.get("delay_max_seconds", 5.0)),
                )
                await page.wait_for_timeout(int(delay * 1000))
            if refresh >= refresh_rounds:
                break
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=15_000)
            await self._select_for_you(page)
        return extracted[:limit]

    async def _detect_challenge(self, page: Page) -> bool:
        selectors = (
            'input[name="challenge_response"]',
            '[data-testid="ocfEnterTextTextInput"]',
            'iframe[title*="captcha" i]',
            'iframe[title*="recaptcha" i]',
        )
        for selector in selectors:
            if await page.locator(selector).count():
                return True
        content = (await page.content()).lower()
        return any(
            value in content
            for value in (
                "verify your identity",
                "unusual login activity",
                "confirm your identity",
                "suspicious activity",
            )
        )

    async def _record_failure(self, now: datetime) -> None:
        previous = await self.state.get_cursor("x_for_you:failures")
        failures = int(previous or 0) + 1
        updates: dict[str, str | None] = {"x_for_you:failures": str(failures)}
        if failures >= int(self.config.get("max_consecutive_failures", 2)):
            updates["x_for_you:cooldown_until"] = (
                now + timedelta(hours=int(self.config.get("cooldown_hours", 6)))
            ).isoformat()
        await self.state.set_cursors(updates)

    def _to_item(
        self, row: dict[str, Any], blob_ref: str, now: datetime
    ) -> SourceItem:
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
                "author": row.get("author", ""),
                "author_display": row.get("user", ""),
                "quoted_text": row.get("quote", ""),
                "links": row.get("links", []),
                "media_urls": row.get("media", []),
                "metrics": row.get("metrics", {}),
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
                    host = urlparse(str(url)).hostname
                    if not host or host.endswith("x.com") or remaining <= 0:
                        continue
                    if url not in cache:
                        try:
                            cache[url] = await fetch_external_metadata(client, str(url))
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


_EXTRACT_POSTS_JS = """
() => [...document.querySelectorAll('article[data-testid="tweet"]')].map(el => {
  const time = el.querySelector('time');
  const status = time?.closest('a')?.getAttribute('href') || '';
  const author = status.split('/status/')[0].split('/').filter(Boolean).pop() || '';
  const text = el.querySelector('[data-testid="tweetText"]')?.innerText || '';
  const user = el.querySelector('[data-testid="User-Name"]')?.innerText || '';
  const quote = [...el.querySelectorAll('[role="link"]')]
    .map(x => x.innerText || '').find(x => x.length > text.length && x.length < 2000) || '';
  const links = [...el.querySelectorAll('a[href]')].map(a => a.href)
    .filter(h => h && !h.includes('/compose/') && !h.includes('/analytics'));
  const media = [...el.querySelectorAll('[data-testid="tweetPhoto"] img, video')]
    .map(x => x.src || x.poster).filter(Boolean);
  const metric = name => el.querySelector(`[data-testid="${name}"]`)?.getAttribute('aria-label') || '';
  return {
    status, author, text, user, quote, links, media,
    datetime: time?.dateTime || '',
    metrics: {reply: metric('reply'), retweet: metric('retweet'), like: metric('like')}
  };
})
"""
