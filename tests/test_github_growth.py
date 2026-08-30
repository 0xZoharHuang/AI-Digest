from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_digest.collectors.github import GitHubCollector, RepoCandidate
from ai_digest.models import FetchManifest, SourceItem
from ai_digest.store import FileStore, StateDB
from ai_digest.utils import json_dumps, sha256_text


def _repo(stars: int) -> dict[str, Any]:
    return {
        "id": 42,
        "full_name": "robot/example",
        "html_url": "https://github.com/robot/example",
        "description": "A robot project",
        "stargazers_count": stars,
        "forks_count": 12,
        "open_issues_count": 3,
        "watchers_count": 7,
        "size": 100,
        "language": "Python",
        "topics": ["robotics"],
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "robot"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-08-30T00:00:00Z",
        "pushed_at": "2026-08-30T00:00:00Z",
        "default_branch": "main",
        "archived": False,
        "disabled": False,
        "fork": False,
    }


def _candidate(store: FileStore, stars: int, *lanes: str) -> RepoCandidate:
    raw_ref = store.write_blob(json_dumps(_repo(stars)), ".json")
    return RepoCandidate(repo=_repo(stars), lanes=set(lanes), raw_refs=[raw_ref])


async def _no_hydration(*args: Any, **kwargs: Any) -> tuple[str, None, list[str]]:
    return "", None, []


async def _commit_snapshot(
    store: FileStore,
    state: StateDB,
    observed_at: datetime,
    stars: int,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "repo_id": "42",
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "full_name": "robot/example",
        "stars": stars,
    }
    snapshot["snapshot_id"] = sha256_text(json_dumps(snapshot))
    snapshot["file_ref"] = store.write_github_snapshot(snapshot)
    await state.commit_github_poll([], [snapshot], {})
    return snapshot


async def _commit_prepared(
    store: FileStore,
    state: StateDB,
    prepared: Any,
) -> None:
    snapshot_ref = store.write_github_snapshot(prepared.snapshot)
    prepared.snapshot["file_ref"] = snapshot_ref
    for item in prepared.items:
        item.payload["snapshot_ref"] = snapshot_ref
        item.payload["snapshot"] = dict(prepared.snapshot)
        store.write_revision(item)
    await state.commit_github_poll(
        prepared.items,
        [prepared.snapshot],
        prepared.event_markers,
    )


@pytest.mark.asyncio
async def test_first_snapshot_is_not_called_growth_and_exposes_null_deltas(
    tmp_path, monkeypatch
):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    collector = GitHubCollector(
        {
            "growth_6h_min_stars": 1,
            "growth_24h_min_stars": 1,
            "growth_7d_min_stars": 1,
        },
        store,
        state,
    )
    monkeypatch.setattr(collector, "_hydrate_repo", _no_hydration)

    prepared = await collector._prepare_repo(
        object(), {}, _candidate(store, 700, "emerging"), datetime(2026, 8, 30, tzinfo=UTC)
    )

    assert prepared.snapshot["star_deltas"] == {"6h": None, "24h": None, "7d": None}
    assert [item.change for item in prepared.items] == ["entered_lane"]
    assert prepared.items[0].payload["snapshot"]["stars"] == 700
    assert prepared.items[0].payload["star_deltas"]["6h"] is None


@pytest.mark.asyncio
async def test_deltas_use_only_a_baseline_at_or_before_each_horizon(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    await _commit_snapshot(store, state, now - timedelta(days=7, hours=1), 100)
    await _commit_snapshot(store, state, now - timedelta(hours=24), 150)
    await _commit_snapshot(store, state, now - timedelta(hours=6), 180)
    collector = GitHubCollector({}, store, state)
    monkeypatch.setattr(collector, "_hydrate_repo", _no_hydration)

    prepared = await collector._prepare_repo(
        object(), {}, _candidate(store, 200, "emerging"), now
    )
    assert prepared.snapshot["star_deltas"] == {"6h": 20, "24h": 50, "7d": 100}
    assert prepared.snapshot["delta_baselines"] == {
        "6h": (now - timedelta(hours=6)).isoformat(),
        "24h": (now - timedelta(hours=24)).isoformat(),
        "7d": (now - timedelta(days=7, hours=1)).isoformat(),
    }

    later_only = StateDB(tmp_path / "later.db")
    await later_only.init()
    await _commit_snapshot(store, later_only, now - timedelta(hours=5, minutes=59), 190)
    collector = GitHubCollector({}, store, later_only)
    monkeypatch.setattr(collector, "_hydrate_repo", _no_hydration)
    prepared = await collector._prepare_repo(
        object(), {}, _candidate(store, 200, "emerging"), now
    )
    assert prepared.snapshot["star_deltas"] == {"6h": None, "24h": None, "7d": None}


@pytest.mark.asyncio
async def test_crossing_and_growth_are_deduplicated_with_cooldown(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    await _commit_snapshot(store, state, now - timedelta(hours=6), 490)
    collector = GitHubCollector(
        {
            "crossing_stars": [500],
            "growth_6h_min_stars": 20,
            "growth_24h_min_stars": 9999,
            "growth_7d_min_stars": 9999,
            "growth_event_cooldown_hours": 24,
        },
        store,
        state,
    )
    monkeypatch.setattr(collector, "_hydrate_repo", _no_hydration)

    first = await collector._prepare_repo(
        object(), {}, _candidate(store, 520, "emerging"), now
    )
    assert {item.change for item in first.items} == {
        "entered_lane",
        "crossed_star_threshold",
        "star_growth",
    }
    growth = next(item for item in first.items if item.change == "star_growth")
    assert growth.payload["event"]["triggered_horizons"] == {"6h": 30}
    await _commit_prepared(store, state, first)

    second = await collector._prepare_repo(
        object(), {}, _candidate(store, 530, "emerging"), now + timedelta(hours=1)
    )
    assert second.items == []
    await _commit_prepared(store, state, second)
    snapshots = await state.github_snapshots("42")
    assert [snapshot["stars"] for snapshot in snapshots] == [490, 520, 530]
    assert all((tmp_path / snapshot["file_ref"]).exists() for snapshot in snapshots)


def test_search_plan_blends_star_activity_and_recent_discovery_within_budget(tmp_path):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    queries = [f"topic:q{index}" for index in range(13)]
    collector = GitHubCollector(
        {"queries": queries, "search_request_budget": 28},
        store,
        state,
    )
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    plan = collector._search_plan(now)

    assert len(plan) == 28
    emerging = [request for request in plan if request.lane == "emerging"]
    assert {request.sort for request in emerging} == {"stars", "updated"}
    assert {request.query for request in emerging} == set(queries)
    assert sum(request.variant == "recent" for request in plan) == 2
    assert "created:>=2026-07-16" in collector._qualified_query(
        next(request for request in plan if request.variant == "recent"), now
    )


@pytest.mark.asyncio
async def test_manifest_failure_does_not_advance_snapshot_or_event_state(
    tmp_path, monkeypatch
):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    collector = GitHubCollector({"queries": []}, store, state)
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    raw_ref = store.write_blob(json_dumps(_repo(700)), ".json")

    async def fake_trending(client, headers, since, observed_at):
        manifest = FetchManifest(
            fetch_id=f"trending-{since}",
            source="github_trending",
            started_at=observed_at,
            completed_at=observed_at,
            request={"url": f"https://github.com/trending?since={since}"},
            fetched_count=1 if since == "daily" else 0,
            parsed_count=1 if since == "daily" else 0,
        )
        rows = [(_repo(700), "emerging", [raw_ref], "")] if since == "daily" else []
        return rows, manifest

    monkeypatch.setattr("ai_digest.collectors.github._github_token", lambda: "")
    monkeypatch.setattr(collector, "_collect_trending", fake_trending)
    monkeypatch.setattr(collector, "_hydrate_repo", _no_hydration)
    original_manifest_writer = store.write_fetch_manifest

    def fail_manifest(manifest):
        raise RuntimeError("manifest fsync failed")

    monkeypatch.setattr(store, "write_fetch_manifest", fail_manifest)
    with pytest.raises(RuntimeError, match="manifest fsync failed"):
        await collector.collect(now)
    assert await state.github_snapshots("42") == []
    assert await state.has_item("github:42:emerging") is False

    monkeypatch.setattr(store, "write_fetch_manifest", original_manifest_writer)
    result = await collector.collect(now)
    assert result.health.new_count == 1
    assert len(await state.github_snapshots("42")) == 1
    assert await state.has_item("github:42:emerging") is True


@pytest.mark.asyncio
async def test_prior_early_repo_is_rechecked_and_emits_crossing_when_search_is_empty(
    tmp_path, monkeypatch
):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    first_at = datetime(2026, 8, 30, 6, tzinfo=UTC)
    initial = GitHubCollector({"crossing_stars": [500]}, store, state)
    monkeypatch.setattr(initial, "_hydrate_repo", _no_hydration)
    prepared = await initial._prepare_repo(
        object(), {}, _candidate(store, 490, "early"), first_at
    )
    await _commit_prepared(store, state, prepared)
    assert [row["full_name"] for row in await state.github_early_watchlist(first_at, 365, 20)] == [
        "robot/example"
    ]

    collector = GitHubCollector(
        {
            "queries": [],
            "crossing_stars": [500],
            "early_watch_days": 365,
            "early_watch_rechecks_per_poll": 1,
        },
        store,
        state,
    )
    second_at = first_at + timedelta(hours=6)
    watched_ref = store.write_blob(json_dumps(_repo(510)), ".json")

    async def no_trending(client, headers, since, observed_at):
        return [], FetchManifest(
            fetch_id=f"empty-{since}",
            source="github_trending",
            started_at=observed_at,
            completed_at=observed_at,
            request={"url": f"https://github.com/trending?since={since}"},
        )

    async def watched_repo(client, headers, full_name):
        assert full_name == "robot/example"
        return _repo(510), watched_ref

    monkeypatch.setattr("ai_digest.collectors.github._github_token", lambda: "")
    monkeypatch.setattr(collector, "_collect_trending", no_trending)
    monkeypatch.setattr(collector, "_repo", watched_repo)
    monkeypatch.setattr(collector, "_hydrate_repo", _no_hydration)
    result = await collector.collect(second_at)

    assert [item.change for item in result.items] == ["crossed_star_threshold"]
    assert result.items[0].payload["event"]["threshold"] == 500
    assert [row["stars"] for row in await state.github_snapshots("42")] == [490, 510]


@pytest.mark.asyncio
async def test_github_commit_rolls_back_item_when_snapshot_index_fails(tmp_path):
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    item = SourceItem(
        item_id="github:42:emerging",
        item_type="github_repository",
        source="github",
        surface="emerging",
        handoff_at=now,
        first_observed_at=now,
    )

    with pytest.raises(KeyError):
        await state.commit_github_poll([item], [{"snapshot_id": "broken"}], {})
    assert await state.has_item(item.item_id) is False


@pytest.mark.asyncio
async def test_github_commit_enforces_growth_cooldown_again_inside_transaction(tmp_path):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    first_snapshot = await _commit_snapshot(store, state, now - timedelta(hours=6), 100)
    first = SourceItem(
        item_id="github:42:growth:first",
        item_type="github_repository",
        source="github",
        surface="growth",
        handoff_at=now,
        first_observed_at=now,
    )
    assert await state.commit_github_poll(
        [first],
        [],
        {"github:42:growth": (first.item_id, now, 24)},
    ) == [first.item_id]

    second_at = now + timedelta(minutes=1)
    second_snapshot = dict(first_snapshot)
    second_snapshot.update(
        {
            "observed_at": second_at.isoformat(),
            "stars": 110,
            "snapshot_id": sha256_text(f"second-{second_at.isoformat()}"),
        }
    )
    second_snapshot["file_ref"] = store.write_github_snapshot(second_snapshot)
    second = first.model_copy(
        update={
            "item_id": "github:42:growth:second",
            "handoff_at": second_at,
            "first_observed_at": second_at,
        }
    )
    assert await state.commit_github_poll(
        [second],
        [second_snapshot],
        {"github:42:growth": (second.item_id, second_at, 24)},
    ) == []
    assert await state.has_item(second.item_id) is False
    assert [row["stars"] for row in await state.github_snapshots("42")] == [100, 110]
