from __future__ import annotations

import asyncio
import platform
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .collectors import (
    ArticleCollector,
    ArxivCollector,
    GitHubCollector,
    HackerNewsCollector,
    HuggingFaceCollector,
    XForYouCollector,
    XListCollector,
)
from .config import RuntimeConfig, SourcesConfig
from .models import (
    CollectorResult,
    HealthStatus,
    RunManifest,
    RunStatus,
    SourceHealth,
    SourceItem,
)
from .store import FileStore, StateDB, load_jsonl, source_group
from .utils import atomic_write_json, atomic_write_jsonl, atomic_write_text


class Phase1Runner:
    def __init__(self, runtime: RuntimeConfig, sources: SourcesConfig):
        self.runtime = runtime
        self.sources = sources
        self.store = FileStore(runtime.runtime_root)
        self.state = StateDB(runtime.runtime_root / "state.db")

    async def initialize(self) -> None:
        await self.state.init()

    async def prune_expired_x_content(self) -> int:
        await self.initialize()
        items = await self.state.pop_expired_x_items()
        for item in items:
            self.store.remove_item_content(item)
        return len(items)

    async def delete_x_post_content(self, post_id: str) -> int:
        await self.initialize()
        items = await self.state.pop_x_post(post_id)
        for item in items:
            self.store.remove_item_content(item)
        return len(items)

    def collectors(self):  # type: ignore[no-untyped-def]
        return [
            XListCollector(self._x_config(self.sources.x_list), self.store, self.state),
            XForYouCollector(self._x_config(self.sources.x_for_you), self.store, self.state),
            GitHubCollector(self.sources.github, self.store, self.state),
            ArxivCollector(self.sources.arxiv, self.store, self.state),
            HuggingFaceCollector(self.sources.huggingface, self.store, self.state),
            HackerNewsCollector(self.sources.hackernews, self.store, self.state),
            ArticleCollector(
                self.sources.articles,
                self.store,
                self.state,
                preview_chars=self.runtime.article_preview_chars,
            ),
        ]

    def _x_config(self, value):  # type: ignore[no-untyped-def]
        return {**value, "retention_days": self.runtime.x_text_retention_days}

    async def collect_only(self, selected: set[str] | None = None) -> list[CollectorResult]:
        await self.initialize()
        now = datetime.now(UTC)
        collectors = [
            collector
            for collector in self.collectors()
            if selected is None or collector.source in selected
        ]
        results = await asyncio.gather(
            *(collector.collect(now) for collector in collectors), return_exceptions=True
        )
        normalized: list[CollectorResult] = []
        for collector, result in zip(collectors, results, strict=True):
            if isinstance(result, BaseException):
                normalized.append(
                    CollectorResult(
                        source=collector.source,
                        health=SourceHealth(
                            source=collector.source,
                            status=HealthStatus.FAILED,
                            errors=[f"{type(result).__name__}: {result}"],
                        ),
                    )
                )
            else:
                normalized.append(result)
        await self._apply_empty_sanity(normalized)
        return normalized

    async def run_daily(self, now: datetime | None = None) -> tuple[RunManifest, Path]:
        await self.initialize()
        wall_started = datetime.now(UTC)
        local_now = (now or wall_started).astimezone(ZoneInfo(self.runtime.timezone))
        date = local_now.date().isoformat()
        attempt, run_dir = self.store.next_attempt_dir(date)
        run_id = f"{date}-a{attempt:04d}-{uuid.uuid4().hex[:8]}"
        initial_end = local_now.astimezone(UTC)
        manifest = RunManifest(
            run_id=run_id,
            date=date,
            attempt=attempt,
            timezone=self.runtime.timezone,
            window_start=initial_end - timedelta(hours=self.runtime.window_hours),
            window_end=initial_end,
            status=RunStatus.RUNNING,
            phases={"phase1": RunStatus.RUNNING},
            versions={
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
        )
        await self.state.record_run(run_id, date, attempt, "running", run_dir)
        atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))

        results = await self.collect_only()
        wall_elapsed = max(datetime.now(UTC) - wall_started, timedelta(0))
        window_end = initial_end + wall_elapsed
        window_start = window_end - timedelta(hours=self.runtime.window_hours)
        manifest.window_start = window_start
        manifest.window_end = window_end
        manifest.source_health = {result.source: result.health for result in results}
        pending = await self.state.pending_items(window_start, window_end)
        grouped: dict[str, list[SourceItem]] = defaultdict(list)
        for item in pending:
            grouped[source_group(item)].append(item)

        phase_dir = run_dir / "01_phase1"
        phase_dir.mkdir(parents=True, exist_ok=True)
        filenames = ["x_list", "x_for_you", "github", "papers", "articles", "hackernews"]
        for name in filenames:
            atomic_write_jsonl(
                phase_dir / f"{name}.jsonl",
                (item.model_dump(mode="json") for item in grouped.get(name, [])),
            )
        written_ids = [
            str(row["item_id"])
            for name in filenames
            for row in load_jsonl(phase_dir / f"{name}.jsonl")
        ]
        expected_ids = [item.item_id for item in pending]
        if (
            len(written_ids) != len(expected_ids)
            or len(written_ids) != len(set(written_ids))
            or set(written_ids) != set(expected_ids)
        ):
            raise RuntimeError("Phase 1 JSONL round-trip coverage validation failed")
        atomic_write_json(
            phase_dir / "source_health.json",
            {key: value.model_dump(mode="json") for key, value in manifest.source_health.items()},
        )
        index = {
            "schema_version": 1,
            "run_id": run_id,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "total_items": len(pending),
            "files": {name: len(grouped.get(name, [])) for name in filenames},
            "item_ids": [item.item_id for item in pending],
        }
        atomic_write_json(phase_dir / "index.json", index)

        phase_status = self._phase_status(results, pending)
        manifest.phases["phase1"] = phase_status
        manifest.status = phase_status
        atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
        atomic_write_text(phase_dir / "PHASE1_COMPLETE", f"{run_id}\n")
        try:
            await self.state.seal_run(
                run_id,
                phase_status.value,
                (item.item_id for item in pending),
            )
        except Exception as error:
            manifest.errors.append(f"Phase 1 sealing failed: {type(error).__name__}: {error}")
            manifest.status = RunStatus.FAILED
            atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
            await self.state.record_run(run_id, date, attempt, RunStatus.FAILED.value, run_dir)
            raise
        return manifest, run_dir

    async def record_skipped_asleep(self, now: datetime | None = None) -> Path | None:
        await self.initialize()
        local_now = (now or datetime.now(UTC)).astimezone(ZoneInfo(self.runtime.timezone))
        date = local_now.date().isoformat()
        if await self.state.has_run_for_date(date):
            return None
        attempt, run_dir = self.store.next_attempt_dir(date)
        run_id = f"{date}-a{attempt:04d}-{uuid.uuid4().hex[:8]}"
        end = local_now.astimezone(UTC)
        manifest = RunManifest(
            run_id=run_id,
            date=date,
            attempt=attempt,
            timezone=self.runtime.timezone,
            window_start=end - timedelta(hours=self.runtime.window_hours),
            window_end=end,
            status=RunStatus.SKIPPED_ASLEEP,
            phases={"phase1": RunStatus.SKIPPED_ASLEEP},
        )
        atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
        await self.state.record_run(run_id, date, attempt, RunStatus.SKIPPED_ASLEEP.value, run_dir)
        return run_dir

    async def _apply_empty_sanity(self, results: list[CollectorResult]) -> None:
        allow_empty = {
            "arxiv": bool(self.sources.arxiv.get("allow_empty", False)),
        }
        for result in results:
            health = result.health
            if health.status in {HealthStatus.DISABLED, HealthStatus.FAILED}:
                continue
            baseline = await self.state.baseline(result.source)
            if (
                health.fetched_count == 0
                and baseline
                and baseline > 0
                and not allow_empty.get(result.source, False)
            ):
                health.status = HealthStatus.FAILED
                health.errors.append(
                    f"suspect_empty: prior fetch returned {baseline}, current fetch returned 0"
                )
                continue
            if health.fetched_count > 0:
                await self.state.set_baseline(result.source, health.fetched_count)

    @staticmethod
    def _phase_status(
        results: list[CollectorResult],
        items: list[SourceItem],
    ) -> RunStatus:
        enabled = [result for result in results if result.health.status != HealthStatus.DISABLED]
        failures = [
            result
            for result in enabled
            if result.health.status in {HealthStatus.FAILED, HealthStatus.PARTIAL}
        ]
        if not items and failures:
            return RunStatus.FAILED
        if failures:
            return RunStatus.PARTIAL
        return RunStatus.SUCCESS
