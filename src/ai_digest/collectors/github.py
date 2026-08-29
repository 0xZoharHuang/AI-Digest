from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import time
from datetime import timedelta
from typing import Any

from bs4 import BeautifulSoup

from ..models import CollectorResult, ContentStatus, HealthStatus, SourceItem, TimeBasis
from ..utils import parse_datetime
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


class GitHubCollector(Collector):
    source = "github"

    def __init__(self, config, store, state):  # type: ignore[no-untyped-def]
        super().__init__(config, store, state)
        self._scheduled_item_ids: set[str] = set()
        self._hydrate_semaphore = asyncio.Semaphore(10)

    async def collect(self, now):  # type: ignore[no-untyped-def]
        if not self.enabled:
            return self.disabled()
        started = time.monotonic()
        token = await asyncio.to_thread(_github_token)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        client = SafeHTTPClient(timeout=35)
        items: list[SourceItem] = []
        manifests = []
        errors: list[str] = []
        fetched = 0
        try:
            for since in ("daily", "weekly"):
                result, manifest = await self._collect_trending(client, headers, since, now)
                items.extend(result)
                fetched += manifest.fetched_count
                manifests.append(manifest)
            cutoff_created = (
                now - timedelta(days=int(self.config.get("created_within_days", 365)))
            ).date()
            cutoff_pushed = (
                now - timedelta(days=int(self.config.get("pushed_within_days", 45)))
            ).date()
            for query in self.config.get("queries", []):
                for lane, stars in (
                    ("early", str(self.config.get("early_stars", "1..499"))),
                    ("emerging", str(self.config.get("emerging_stars", "500..5000"))),
                ):
                    qualified = (
                        f"{query} stars:{stars} created:>={cutoff_created} "
                        f"pushed:>={cutoff_pushed} fork:false archived:false"
                    )
                    result, manifest = await self._search(client, headers, qualified, lane, now)
                    items.extend(result)
                    fetched += manifest.fetched_count
                    manifests.append(manifest)
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

    async def _collect_trending(self, client, headers, since, now):  # type: ignore[no-untyped-def]
        url = f"https://github.com/trending?since={since}"
        manifest = new_fetch_manifest("github_trending", url)
        response = await client.request("GET", url, headers={"Accept": "text/html"})
        blob = self.store.write_blob(response.text, ".html")
        soup = BeautifulSoup(response.text, "html.parser")
        rows = []
        for article in soup.select("article.Box-row"):
            link = article.select_one("h2 a")
            if link is None:
                continue
            name = str(link.get("href", "")).strip("/")
            if name.count("/") != 1:
                continue
            text = " ".join(article.get_text(" ", strip=True).split())
            rows.append((name, text))
        tasks = []
        for name, trend_text in rows:
            repo = await self._repo(client, headers, name)
            if not repo:
                continue
            item_id = f"github:{repo['id']}:trending_{since}"
            if item_id in self._scheduled_item_ids or await self.state.has_item(item_id):
                continue
            self._scheduled_item_ids.add(item_id)
            tasks.append(
                self._repo_item(client, headers, repo, f"trending_{since}", now, [blob], trend_text)
            )
        items = list(await asyncio.gather(*tasks)) if tasks else []
        manifest.blob_refs = [blob]
        return items, finish_manifest(
            manifest,
            response=response,
            fetched_count=len(rows),
            parsed_count=len(items),
        )

    async def _search(self, client, headers, query, lane, now):  # type: ignore[no-untyped-def]
        url = "https://api.github.com/search/repositories"
        manifest = new_fetch_manifest(self.source, url)
        manifest.request["query"] = query
        response = await client.request(
            "GET",
            url,
            headers=headers,
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": int(self.config.get("per_query", 20)),
            },
        )
        blob = self.store.write_blob(response.text, ".json")
        payload = response.json()
        repos = payload.get("items") or []
        tasks = []
        for repo in repos:
            item_id = f"github:{repo['id']}:{lane}"
            if item_id in self._scheduled_item_ids or await self.state.has_item(item_id):
                continue
            self._scheduled_item_ids.add(item_id)
            tasks.append(self._repo_item(client, headers, repo, lane, now, [blob]))
        items = list(await asyncio.gather(*tasks)) if tasks else []
        manifest.blob_refs = [blob]
        return items, finish_manifest(
            manifest,
            response=response,
            fetched_count=len(repos),
            parsed_count=len(items),
        )

    async def _repo(self, client, headers, full_name):  # type: ignore[no-untyped-def]
        response = await client.request(
            "GET", f"https://api.github.com/repos/{full_name}", headers=headers
        )
        return response.json()

    async def _repo_item(
        self,
        client,
        headers,
        repo,
        lane,
        now,
        raw_refs,
        trending_text="",
    ):  # type: ignore[no-untyped-def]
        async with self._hydrate_semaphore:
            full_name = repo["full_name"]
            readme = ""
            release: dict[str, Any] | None = None
            try:
                response = await client.request(
                    "GET", f"https://api.github.com/repos/{full_name}/readme", headers=headers
                )
                raw_refs.append(self.store.write_blob(response.text, ".json"))
                content = response.json().get("content", "").replace("\n", "")
                readme = base64.b64decode(content).decode("utf-8", errors="replace")[:4000]
            except Exception:
                pass
            try:
                response = await client.request(
                    "GET",
                    f"https://api.github.com/repos/{full_name}/releases/latest",
                    headers=headers,
                )
                raw_refs.append(self.store.write_blob(response.text, ".json"))
                value = response.json()
                release = {
                    "tag": value.get("tag_name"),
                    "name": value.get("name"),
                    "published_at": value.get("published_at"),
                    "body": (value.get("body") or "")[:4000],
                    "url": value.get("html_url"),
                }
            except Exception:
                pass
        created = parse_datetime(repo.get("created_at"))
        return SourceItem(
            item_id=f"github:{repo['id']}:{lane}",
            item_type="github_repository",
            source=self.source,
            surface=lane,
            change="entered_lane",
            occurred_at=created,
            first_observed_at=now,
            handoff_at=now,
            time_basis=TimeBasis.OBSERVED,
            content_status=ContentStatus.PREVIEW,
            raw_refs=raw_refs,
            payload={
                "repo_id": repo["id"],
                "full_name": full_name,
                "url": repo.get("html_url"),
                "description": repo.get("description") or "",
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "open_issues": repo.get("open_issues_count", 0),
                "language": repo.get("language"),
                "topics": repo.get("topics") or [],
                "license": (repo.get("license") or {}).get("spdx_id"),
                "created_at": repo.get("created_at"),
                "pushed_at": repo.get("pushed_at"),
                "archived": repo.get("archived", False),
                "lane": lane,
                "trending_text": trending_text,
                "readme_preview": readme,
                "latest_release": release,
            },
        )


def _github_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
