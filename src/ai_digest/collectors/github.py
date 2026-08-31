from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from ..models import (
    CollectorResult,
    ContentStatus,
    FetchManifest,
    HealthStatus,
    SourceItem,
    TimeBasis,
)
from ..utils import json_dumps, parse_datetime, sha256_text
from .base import Collector, SafeHTTPClient, finish_manifest, health_from, new_fetch_manifest


@dataclass(frozen=True)
class SearchRequest:
    query: str
    lane: str
    stars: str
    sort: str
    variant: str = "standard"


@dataclass
class RepoCandidate:
    repo: dict[str, Any]
    lanes: set[str] = field(default_factory=set)
    raw_refs: list[str] = field(default_factory=list)
    trending_text: dict[str, str] = field(default_factory=dict)


@dataclass
class PreparedRepo:
    snapshot: dict[str, Any]
    items: list[SourceItem]
    event_markers: dict[str, tuple[str, datetime, int]]


CandidateRow = tuple[dict[str, Any], str | None, list[str], str]


class GitHubCollector(Collector):
    source = "github"

    def __init__(self, config, store, state):  # type: ignore[no-untyped-def]
        super().__init__(config, store, state)
        self._hydrate_semaphore = asyncio.Semaphore(10)
        self._repo_semaphore = asyncio.Semaphore(10)
        self._prepare_semaphore = asyncio.Semaphore(20)
        self._repo_cache: dict[str, tuple[dict[str, Any], str]] = {}

    async def collect(self, now):  # type: ignore[no-untyped-def]
        if not self.enabled:
            return self.disabled()
        now = now.astimezone(UTC)
        self._repo_cache.clear()
        started = time.monotonic()
        token = await asyncio.to_thread(_github_token)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        client = SafeHTTPClient(timeout=35)
        candidates: dict[str, RepoCandidate] = {}
        manifests = []
        errors: list[str] = []
        warnings: list[str] = []
        fetched = 0
        try:
            for since in ("daily", "weekly"):
                rows, manifest = await self._collect_trending(client, headers, since, now)
                for repo, lane, raw_refs, trend_text in rows:
                    self._merge_candidate(
                        candidates,
                        repo,
                        lane,
                        raw_refs,
                        trend_text=trend_text,
                    )
                fetched += manifest.fetched_count
                manifests.append(manifest)
                errors.extend(manifest.errors)

            for request in self._search_plan(now):
                rows, manifest = await self._search(client, headers, request, now)
                for repo, lane, raw_refs, _ in rows:
                    self._merge_candidate(candidates, repo, lane, raw_refs)
                fetched += manifest.fetched_count
                manifests.append(manifest)
                errors.extend(manifest.errors)

            watchlist = await self.state.github_early_watchlist(
                now,
                int(self.config.get("early_watch_days", 365)),
                int(self.config.get("early_watch_rechecks_per_poll", 20)),
            )
            watchlist = [row for row in watchlist if row["repo_id"] not in candidates]
            if watchlist:
                rows, manifest = await self._collect_watched(client, headers, watchlist, now)
                for repo, lane, raw_refs, _ in rows:
                    self._merge_candidate(candidates, repo, lane, raw_refs)
                fetched += manifest.fetched_count
                manifests.append(manifest)
                errors.extend(manifest.errors)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

        candidate_total = len(candidates)
        candidate_limit = int(self.config.get("candidate_processing_limit", 1500))
        candidate_values = list(candidates.values())[:candidate_limit]
        if candidate_total > candidate_limit:
            warnings.append(
                f"candidate processing cap reached: {candidate_total} discovered, "
                f"{candidate_limit} snapshotted; query order rotates across polls"
            )
        try:
            async def prepare(candidate: RepoCandidate) -> PreparedRepo:
                async with self._prepare_semaphore:
                    return await self._prepare_repo(client, headers, candidate, now)

            prepared = list(
                await asyncio.gather(
                    *(prepare(candidate) for candidate in candidate_values)
                )
            )
        finally:
            await client.close()

        items = [item for value in prepared for item in value.items]
        snapshots = [value.snapshot for value in prepared]
        event_markers = {
            key: marker for value in prepared for key, marker in value.event_markers.items()
        }

        # These are all atomic file writes. StateDB advances the immutable snapshot
        # index and event markers only after every required file is durable.
        for value in prepared:
            snapshot = value.snapshot
            snapshot_ref = self.store.write_github_snapshot(snapshot)
            snapshot["file_ref"] = snapshot_ref
            snapshot_blob = self.store.write_blob(json_dumps(snapshot), ".github-snapshot.json")
            for item in value.items:
                item.raw_refs = list(dict.fromkeys([snapshot_blob, *item.raw_refs]))
                item.payload["snapshot_ref"] = snapshot_ref
                item.payload["snapshot"] = dict(snapshot)
                self.store.write_revision(item)
        for manifest in manifests:
            self.store.write_fetch_manifest(manifest)

        inserted = await self.state.commit_github_poll(items, snapshots, event_markers)
        status = HealthStatus.SUCCESS
        if errors and snapshots:
            status = HealthStatus.PARTIAL
        elif errors:
            status = HealthStatus.FAILED
        result = CollectorResult(
            source=self.source,
            items=items,
            manifests=manifests,
            health=health_from(
                self.source,
                started,
                status,
                fetched,
                len(snapshots),
                len(inserted),
                errors,
                warnings,
            ),
        )
        result.health.surfaces = {
            "bounded_discovery": {
                "candidate_total": candidate_total,
                "candidate_processed": len(candidate_values),
                "candidate_cap": candidate_limit,
            }
        }
        result.health.raw_receipts_complete = not bool(errors)
        return result

    def _search_plan(self, now: datetime) -> list[SearchRequest]:
        queries = [str(value).strip() for value in self.config.get("queries", []) if str(value)]
        if not queries:
            return []
        budget = max(0, min(int(self.config.get("search_request_budget", 28)), 30))
        slot = int(now.astimezone(UTC).timestamp() // timedelta(hours=6).total_seconds())
        start = slot % len(queries)
        ordered = [queries[(start + offset) % len(queries)] for offset in range(len(queries))]

        plan: list[SearchRequest] = []
        emerging_stars = str(self.config.get("emerging_stars", "500..5000"))
        early_stars = str(self.config.get("early_stars", "1..499"))
        for offset, query in enumerate(ordered):
            # Half of the 500..5000 lanes are activity-sorted on every poll; the
            # halves swap every six hours. This avoids returning only incumbents.
            sort = "stars" if (slot + offset) % 2 == 0 else "updated"
            plan.append(SearchRequest(query, "emerging", emerging_stars, sort))
        for query in ordered:
            plan.append(SearchRequest(query, "early", early_stars, "updated"))
        for query in ordered:
            plan.append(
                SearchRequest(query, "emerging", emerging_stars, "updated", variant="recent")
            )
        return plan[:budget]

    def _qualified_query(self, request: SearchRequest, now: datetime) -> str:
        created_days = int(self.config.get("created_within_days", 365))
        if request.variant == "recent":
            created_days = int(self.config.get("recent_created_within_days", 45))
        pushed_days = int(self.config.get("pushed_within_days", 45))
        cutoff_created = (now - timedelta(days=created_days)).date()
        cutoff_pushed = (now - timedelta(days=pushed_days)).date()
        return (
            f"{request.query} stars:{request.stars} created:>={cutoff_created} "
            f"pushed:>={cutoff_pushed} fork:false archived:false"
        )

    async def _collect_trending(self, client, headers, since, now):  # type: ignore[no-untyped-def]
        url = f"https://github.com/trending?since={since}"
        manifest = new_fetch_manifest("github_trending", url, now)
        response = await client.request("GET", url, headers={"Accept": "text/html"})
        blob = self.store.write_blob(response.text, ".html")
        soup = BeautifulSoup(response.text, "html.parser")
        rows: list[tuple[str, str]] = []
        for article in soup.select("article.Box-row"):
            link = article.select_one("h2 a")
            if link is None:
                continue
            name = str(link.get("href", "")).strip("/")
            if name.count("/") != 1:
                continue
            text = " ".join(article.get_text(" ", strip=True).split())
            rows.append((name, text))

        results = await asyncio.gather(
            *[self._repo(client, headers, name) for name, _ in rows],
            return_exceptions=True,
        )
        candidates = []
        row_errors: list[str] = []
        for (name, trend_text), result in zip(rows, results, strict=True):
            if isinstance(result, BaseException):
                row_errors.append(f"{name}: {type(result).__name__}: {result}")
                continue
            repo, repo_ref = result
            candidates.append((repo, f"trending_{since}", [blob, repo_ref], trend_text))
        manifest.blob_refs = [blob]
        status = HealthStatus.PARTIAL if row_errors and candidates else HealthStatus.SUCCESS
        if row_errors and not candidates:
            status = HealthStatus.FAILED
        return candidates, finish_manifest(
            manifest,
            response=response,
            fetched_count=len(rows),
            parsed_count=len(candidates),
            status=status,
            errors=row_errors,
        )

    async def _search(
        self,
        client: SafeHTTPClient,
        headers: dict[str, str],
        request: SearchRequest,
        now: datetime,
    ) -> tuple[list[CandidateRow], FetchManifest]:
        url = "https://api.github.com/search/repositories"
        manifest = new_fetch_manifest(self.source, url, now)
        query = self._qualified_query(request, now)
        manifest.request.update(
            {
                "query": query,
                "lane": request.lane,
                "sort": request.sort,
                "variant": request.variant,
            }
        )
        response = await client.request(
            "GET",
            url,
            headers=headers,
            params={
                "q": query,
                "sort": request.sort,
                "order": "desc",
                "per_page": min(100, int(self.config.get("per_query", 100))),
            },
        )
        blob = self.store.write_blob(response.text, ".json")
        payload = response.json()
        repos = payload.get("items") or []
        candidates: list[CandidateRow] = [
            (repo, request.lane, [blob], "")
            for repo in repos
            if isinstance(repo, dict) and repo.get("id") is not None and repo.get("full_name")
        ]
        manifest.blob_refs = [blob]
        return candidates, finish_manifest(
            manifest,
            response=response,
            fetched_count=len(repos),
            parsed_count=len(candidates),
        )

    async def _collect_watched(
        self,
        client: SafeHTTPClient,
        headers: dict[str, str],
        watchlist: list[dict[str, str]],
        now: datetime,
    ) -> tuple[list[CandidateRow], FetchManifest]:
        url = "https://api.github.com/repos/{owner}/{repo}"
        manifest = new_fetch_manifest("github_watch", url, now)
        manifest.request.update(
            {
                "kind": "early_lane_recheck",
                "repositories": [row["full_name"] for row in watchlist],
            }
        )
        results = await asyncio.gather(
            *[self._repo(client, headers, row["full_name"]) for row in watchlist],
            return_exceptions=True,
        )
        candidates: list[CandidateRow] = []
        errors: list[str] = []
        for row, result in zip(watchlist, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(
                    f"{row['full_name']}: {type(result).__name__}: {result}"
                )
                continue
            repo, raw_ref = result
            candidates.append((repo, None, [raw_ref], ""))
        manifest.blob_refs = [raw_refs[0] for _, _, raw_refs, _ in candidates]
        status = HealthStatus.PARTIAL if errors and candidates else HealthStatus.SUCCESS
        if errors and not candidates:
            status = HealthStatus.FAILED
        return candidates, finish_manifest(
            manifest,
            fetched_count=len(watchlist),
            parsed_count=len(candidates),
            status=status,
            errors=errors,
        )

    async def _repo(
        self, client: SafeHTTPClient, headers: dict[str, str], full_name: str
    ) -> tuple[dict[str, Any], str]:
        cached = self._repo_cache.get(full_name)
        if cached is not None:
            return cached
        async with self._repo_semaphore:
            cached = self._repo_cache.get(full_name)
            if cached is not None:
                return cached
            response = await client.request(
                "GET", f"https://api.github.com/repos/{full_name}", headers=headers
            )
            raw_ref = self.store.write_blob(response.text, ".json")
            value = response.json(), raw_ref
            self._repo_cache[full_name] = value
            return value

    def _merge_candidate(
        self,
        candidates: dict[str, RepoCandidate],
        repo: dict[str, Any],
        lane: str | None,
        raw_refs: list[str],
        *,
        trend_text: str = "",
    ) -> None:
        repo_id = str(int(repo["id"]))
        candidate = candidates.get(repo_id)
        if candidate is None:
            candidate = RepoCandidate(repo=dict(repo))
            candidates[repo_id] = candidate
        else:
            candidate.repo.update({key: value for key, value in repo.items() if value is not None})
        if lane is not None:
            candidate.lanes.add(lane)
        candidate.raw_refs = list(dict.fromkeys([*candidate.raw_refs, *raw_refs]))
        if lane is not None and trend_text:
            candidate.trending_text[lane] = trend_text

    async def _prepare_repo(
        self,
        client: SafeHTTPClient,
        headers: dict[str, str],
        candidate: RepoCandidate,
        now: datetime,
    ) -> PreparedRepo:
        repo = candidate.repo
        repo_id = str(int(repo["id"]))
        snapshot = self._snapshot(repo, candidate, now)
        thresholds = self._crossing_thresholds()
        lane_ids = {lane: f"github:{repo_id}:{lane}" for lane in sorted(candidate.lanes)}
        crossing_ids = {
            threshold: f"github:{repo_id}:crossed_{threshold}" for threshold in thresholds
        }
        growth_key = f"github:{repo_id}:growth"
        context = await self.state.github_repo_context(
            repo_id,
            now,
            [*lane_ids.values(), *crossing_ids.values()],
            growth_key,
        )

        baselines = context["baselines"]
        star_deltas: dict[str, int | None] = {}
        delta_baselines: dict[str, str | None] = {}
        for label in ("6h", "24h", "7d"):
            baseline = baselines[label]
            if baseline is None:
                star_deltas[label] = None
                delta_baselines[label] = None
            else:
                star_deltas[label] = int(snapshot["stars"]) - int(baseline["stars"])
                delta_baselines[label] = str(baseline["observed_at"])
        snapshot["star_deltas"] = star_deltas
        snapshot["delta_baselines"] = delta_baselines

        existing = set(context["existing_item_ids"])
        event_specs: list[tuple[str, str, str, dict[str, Any]]] = []
        for lane, item_id in lane_ids.items():
            if item_id not in existing:
                event_specs.append(
                    (item_id, lane, "entered_lane", {"kind": "entered_lane", "lane": lane})
                )

        latest = context["latest"]
        previous_metadata = dict(latest.get("metadata") or {}) if latest is not None else {}
        if latest is not None:
            previous_stars = int(latest["stars"])
            for threshold, item_id in crossing_ids.items():
                if item_id not in existing and previous_stars < threshold <= int(snapshot["stars"]):
                    event_specs.append(
                        (
                            item_id,
                            "growth",
                            "crossed_star_threshold",
                            {
                                "kind": "crossed_star_threshold",
                                "threshold": threshold,
                                "previous_stars": previous_stars,
                            },
                        )
                    )

        readme = ""
        release: dict[str, Any] | None = None
        extra_refs: list[str] = []
        pushed_changed = bool(
            latest is not None
            and snapshot.get("pushed_at")
            and snapshot.get("pushed_at") != previous_metadata.get("pushed_at")
        )
        meaningful_existing_event = any(
            change != "entered_lane" for _, _, change, _ in event_specs
        )
        if meaningful_existing_event or pushed_changed:
            readme, release, extra_refs = await self._hydrate_repo(
                client, headers, str(repo["full_name"])
            )
        snapshot["latest_release"] = release
        snapshot_id = sha256_text(json_dumps(snapshot))
        snapshot["snapshot_id"] = snapshot_id

        if latest is not None:
            changed_fields = {
                key: {"before": previous_metadata.get(key), "after": snapshot.get(key)}
                for key in ("archived", "disabled", "default_branch", "license")
                if key in previous_metadata and previous_metadata.get(key) != snapshot.get(key)
            }
            if changed_fields:
                event_specs.append(
                    (
                        f"github:{repo_id}:metadata:{snapshot_id[:16]}",
                        "metadata",
                        "metadata_change",
                        {"kind": "metadata_change", "changes": changed_fields},
                    )
                )
            previous_release = previous_metadata.get("latest_release") or {}
            if (
                release
                and previous_metadata.get("latest_release") is not None
                and release.get("tag")
                and release.get("tag") != previous_release.get("tag")
            ):
                event_specs.append(
                    (
                        f"github:{repo_id}:release:{snapshot_id[:16]}",
                        "release",
                        "release_published",
                        {
                            "kind": "release_published",
                            "previous_tag": previous_release.get("tag"),
                            "tag": release.get("tag"),
                            "published_at": release.get("published_at"),
                        },
                    )
                )

        triggers = {
            label: delta
            for label, delta in star_deltas.items()
            if delta is not None and delta >= self._growth_threshold(label)
        }
        growth_event_at = parse_datetime(context["growth_event_at"])
        cooldown = timedelta(hours=int(self.config.get("growth_event_cooldown_hours", 24)))
        cooldown_elapsed = growth_event_at is None or now - growth_event_at >= cooldown
        new_stars = latest is not None and int(snapshot["stars"]) > int(latest["stars"])
        event_markers: dict[str, tuple[str, datetime, int]] = {}
        if triggers and new_stars and cooldown_elapsed:
            item_id = f"github:{repo_id}:growth:{snapshot_id[:16]}"
            event_specs.append(
                (
                    item_id,
                    "growth",
                    "star_growth",
                    {"kind": "star_growth", "triggered_horizons": triggers},
                )
            )
            event_markers[growth_key] = (
                item_id,
                now,
                int(self.config.get("growth_event_cooldown_hours", 24)),
            )

        raw_refs = list(dict.fromkeys([*candidate.raw_refs, *extra_refs]))
        base_payload = self._event_payload(
            repo,
            candidate,
            snapshot,
            readme=readme,
            release=release,
        )
        created = parse_datetime(repo.get("created_at"))
        items = [
            SourceItem(
                item_id=item_id,
                item_type="github_repository",
                source=self.source,
                surface=surface,
                change=change,
                occurred_at=created if change == "entered_lane" else None,
                first_observed_at=now,
                handoff_at=now,
                ready_at=now,
                entity_key=f"github:{repo_id}",
                content_hash=snapshot_id,
                time_basis=TimeBasis.OBSERVED,
                content_status=ContentStatus.PREVIEW,
                raw_refs=list(raw_refs),
                payload={**base_payload, "event": event},
            )
            for item_id, surface, change, event in event_specs
        ]
        return PreparedRepo(snapshot=snapshot, items=items, event_markers=event_markers)

    def _snapshot(
        self, repo: dict[str, Any], candidate: RepoCandidate, now: datetime
    ) -> dict[str, Any]:
        license_value = repo.get("license") or {}
        owner = repo.get("owner") or {}
        return {
            "schema_version": 1,
            "repo_id": str(int(repo["id"])),
            "observed_at": now.astimezone(UTC).isoformat(),
            "full_name": str(repo["full_name"]),
            "url": repo.get("html_url"),
            "description": repo.get("description") or "",
            "stars": int(repo.get("stargazers_count") or 0),
            "forks": int(repo.get("forks_count") or 0),
            "open_issues": int(repo.get("open_issues_count") or 0),
            "watchers": int(repo.get("subscribers_count") or repo.get("watchers_count") or 0),
            "size_kb": int(repo.get("size") or 0),
            "language": repo.get("language"),
            "topics": repo.get("topics") or [],
            "license": license_value.get("spdx_id") if isinstance(license_value, dict) else None,
            "owner": owner.get("login") if isinstance(owner, dict) else None,
            "created_at": repo.get("created_at"),
            "updated_at": repo.get("updated_at"),
            "pushed_at": repo.get("pushed_at"),
            "default_branch": repo.get("default_branch"),
            "archived": bool(repo.get("archived", False)),
            "disabled": bool(repo.get("disabled", False)),
            "fork": bool(repo.get("fork", False)),
            "lanes": sorted(candidate.lanes),
        }

    def _event_payload(
        self,
        repo: dict[str, Any],
        candidate: RepoCandidate,
        snapshot: dict[str, Any],
        *,
        readme: str,
        release: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "repo_id": repo["id"],
            "full_name": repo["full_name"],
            "url": repo.get("html_url"),
            "description": repo.get("description") or "",
            "language": repo.get("language"),
            "topics": repo.get("topics") or [],
            "lanes": sorted(candidate.lanes),
            "trending_text": dict(candidate.trending_text),
            "readme_preview": readme,
            "latest_release": release,
            "snapshot": dict(snapshot),
            "star_deltas": dict(snapshot["star_deltas"]),
            "delta_baselines": dict(snapshot["delta_baselines"]),
        }

    async def _hydrate_repo(
        self, client: SafeHTTPClient, headers: dict[str, str], full_name: str
    ) -> tuple[str, dict[str, Any] | None, list[str]]:
        readme = ""
        release: dict[str, Any] | None = None
        raw_refs: list[str] = []
        async with self._hydrate_semaphore:
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
        return readme, release, raw_refs

    def _crossing_thresholds(self) -> list[int]:
        values = self.config.get("crossing_stars", [500, 5000])
        return sorted({int(value) for value in values if int(value) >= 0})

    def _growth_threshold(self, label: str) -> int:
        keys = {
            "6h": "growth_6h_min_stars",
            "24h": "growth_24h_min_stars",
            "7d": "growth_7d_min_stars",
        }
        defaults = {"6h": 25, "24h": 75, "7d": 250}
        return int(self.config.get(keys[label], defaults[label]))


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
