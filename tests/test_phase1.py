from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from ai_digest.config import RuntimeConfig, SourcesConfig
from ai_digest.models import CollectorResult, HealthStatus, RunStatus, SourceHealth, SourceItem
from ai_digest.phase1 import Phase1Runner


@pytest.mark.asyncio
async def test_phase1_seals_typed_files_without_marking_delivery(tmp_path, monkeypatch):
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared")
    runner = Phase1Runner(runtime, SourcesConfig())
    await runner.initialize()
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    item = SourceItem(
        item_id="hn:top:1",
        item_type="hn_story",
        source="hackernews",
        surface="top",
        first_observed_at=now,
        handoff_at=now,
        payload={"title": "story"},
    )
    await runner.state.put_items([item])

    async def fake_collect():
        return [
            CollectorResult(
                source="hackernews",
                health=SourceHealth(
                    source="hackernews",
                    status=HealthStatus.SUCCESS,
                    fetched_count=10,
                    parsed_count=1,
                    new_count=1,
                ),
            )
        ]

    monkeypatch.setattr(runner, "collect_only", fake_collect)
    manifest, run_dir = await runner.run_daily(now + timedelta(minutes=5))
    phase = run_dir / "01_phase1"
    assert (phase / "PHASE1_COMPLETE").exists()
    assert manifest.phases["phase1"].value == "success"
    rows = [json.loads(line) for line in (phase / "hackernews.jsonl").read_text().splitlines()]
    assert rows[0]["item_id"] == "hn:top:1"
    assert json.loads((phase / "index.json").read_text())["total_items"] == 1
    assert await runner.state.list_sealed_unqueued_runs() == [(manifest.run_id, run_dir)]


@pytest.mark.asyncio
async def test_phase1_window_closes_after_collection(tmp_path, monkeypatch):
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared")
    runner = Phase1Runner(runtime, SourcesConfig())
    await runner.initialize()
    run_started = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    observed: datetime | None = None

    async def fake_collect():
        nonlocal observed
        observed = run_started + timedelta(milliseconds=1)
        item = SourceItem(
            item_id="hn:arrived-during-collection",
            item_type="hn_story",
            source="hackernews",
            surface="top",
            first_observed_at=observed,
            handoff_at=observed,
            payload={"title": "new during collection"},
        )
        await runner.state.put_items([item])
        await asyncio.sleep(0.01)
        return [
            CollectorResult(
                source="hackernews",
                health=SourceHealth(
                    source="hackernews",
                    status=HealthStatus.SUCCESS,
                    fetched_count=1,
                    parsed_count=1,
                    new_count=1,
                ),
            )
        ]

    monkeypatch.setattr(runner, "collect_only", fake_collect)
    manifest, run_dir = await runner.run_daily(run_started)

    rows = [
        json.loads(line)
        for line in (run_dir / "01_phase1" / "hackernews.jsonl").read_text().splitlines()
    ]
    assert [row["item_id"] for row in rows] == ["hn:arrived-during-collection"]
    assert observed is not None
    assert manifest.window_end > observed


@pytest.mark.asyncio
async def test_suspect_empty_does_not_replace_baseline(tmp_path):
    runner = Phase1Runner(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared"),
        SourcesConfig(),
    )
    await runner.initialize()
    await runner.state.set_baseline("arxiv", 700)
    result = CollectorResult(
        source="arxiv",
        health=SourceHealth(source="arxiv", status=HealthStatus.SUCCESS, fetched_count=0),
    )
    await runner._apply_empty_sanity([result])
    assert result.health.status == HealthStatus.FAILED
    assert "suspect_empty" in result.health.errors[0]
    assert await runner.state.baseline("arxiv") == 700


@pytest.mark.asyncio
async def test_phase1_x_content_pruning_and_explicit_delete(tmp_path):
    runner = Phase1Runner(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared"),
        SourcesConfig(),
    )
    await runner.initialize()
    now = datetime.now(UTC)
    expired = SourceItem(
        item_id="x_list:1",
        item_type="x_post",
        source="x_list",
        surface="private_list",
        handoff_at=now,
        first_observed_at=now,
        expires_at=now - timedelta(seconds=1),
        raw_refs=[runner.store.write_blob("expired")],
    )
    current = expired.model_copy(
        update={
            "item_id": "x_for_you:2",
            "expires_at": now + timedelta(days=30),
            "raw_refs": [runner.store.write_blob("current")],
        }
    )
    await runner.state.put_items([expired, current])
    runner.store.write_revision(expired)
    runner.store.write_revision(current)
    assert await runner.prune_expired_x_content() == 1
    assert await runner.delete_x_post_content("2") == 1


def test_required_disabled_source_makes_phase_partial():
    result = CollectorResult(
        source="x_list",
        health=SourceHealth(source="x_list", status=HealthStatus.DISABLED),
    )
    assert Phase1Runner._phase_status([result], [], {"x_list"}) == RunStatus.FAILED


@pytest.mark.asyncio
async def test_skipped_asleep_is_recorded_once(tmp_path):
    runner = Phase1Runner(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared"),
        SourcesConfig(),
    )
    now = datetime(2026, 8, 30, 2, 0, tzinfo=UTC)
    run_dir = await runner.record_skipped_asleep(now)
    assert run_dir is not None
    manifest = json.loads((run_dir / "00_run_manifest.json").read_text())
    assert manifest["status"] == "skipped_asleep"
    assert await runner.record_skipped_asleep(now) is None
