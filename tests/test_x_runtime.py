from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from ai_digest.config import RuntimeConfig, SourcesConfig
from ai_digest.models import RoutingOutput, SourceItem
from ai_digest.x_auth import XTokens
from ai_digest.x_compliance import (
    XComplianceRunner,
    _group_events,
    _manifest_run_id,
    _overall_status,
    _parse_compliance_events,
    _rebuild_run,
    _rewrite_x_handoffs,
    _row_post_id,
    _run_dir,
    _thread_ids,
)
from ai_digest.x_setup import _members, build_private_list


@pytest.mark.asyncio
async def test_list_plan_is_full_deduplicated_seed_union(tmp_path, monkeypatch):
    seed_rows = {
        "a": [
            {"id": "1", "username": "one", "name": "One"},
            {"id": "2", "username": "two", "name": "Two"},
        ],
        "b": [
            {"id": "2", "username": "two", "name": "Two"},
            {"id": "3", "username": "three", "name": "Three"},
        ],
    }

    async def fake_members(client, store, tokens, list_id):  # type: ignore[no-untyped-def]
        return seed_rows[list_id], tokens

    monkeypatch.setattr("ai_digest.x_setup.XTokenStore.load", lambda self: XTokens("token"))
    monkeypatch.setattr("ai_digest.x_setup._members", fake_members)
    output = tmp_path / "plan.json"
    plan = await build_private_list(
        seed_list_ids=["a", "b"],
        target_list_id=None,
        target_members=None,
        output_path=output,
    )
    assert plan["unique_seed_members"] == 3
    assert plan["planned_members"] == 3
    member_two = next(row for row in plan["members"] if row["id"] == "2")
    assert member_two["seed_list_ids"] == ["a", "b"]
    assert json.loads(output.read_text())["private"] is True


@pytest.mark.asyncio
async def test_list_members_uses_get_and_follows_pagination(monkeypatch):
    calls = []

    async def fake_request(client, store, tokens, method, url, **kwargs):
        calls.append((method, url, kwargs["params"].copy()))
        if len(calls) == 1:
            payload = {"data": [{"id": "1"}], "meta": {"next_token": "next"}}
        else:
            payload = {"data": [{"id": "2"}], "meta": {}}
        return _Response(payload), tokens

    monkeypatch.setattr("ai_digest.x_setup._request", fake_request)
    tokens = XTokens("token")
    rows, returned = await _members(SimpleNamespace(), SimpleNamespace(), tokens, "list-1")
    assert [row["id"] for row in rows] == ["1", "2"]
    assert returned == tokens
    assert [call[0] for call in calls] == ["GET", "GET"]
    assert calls[0][1].endswith("/lists/list-1/members")
    assert calls[1][2]["pagination_token"] == "next"


def test_compliance_event_parser_accepts_flat_and_nested_ndjson():
    text = "\n".join(
        [
            json.dumps({"id": "1", "event": "deleted"}),
            json.dumps({"protected": {"id": "2"}}),
            json.dumps({"tweet_id": "3", "action": "scrub_geo"}),
        ]
    )
    assert _parse_compliance_events(text) == {
        "1": "deleted",
        "2": "protected",
        "3": "scrub_geo",
    }


def test_compliance_rewrites_x_handoff_and_index(tmp_path):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime",
        shared_runtime_root=tmp_path / "runtime" / "queue",
    )
    run_dir = runtime.runtime_root / "runs" / "2026-08-30" / "attempt-0001"
    phase = run_dir / "01_phase1"
    phase.mkdir(parents=True)
    (run_dir / "00_run_manifest.json").write_text(json.dumps({"run_id": "run-x"}))
    (phase / "x_list.jsonl").write_text(
        json.dumps({"item_id": "x_list:1", "payload": {"post_id": "1"}}) + "\n"
        + json.dumps({"item_id": "x_list:2", "payload": {"post_id": "2"}})
        + "\n"
    )
    for name in ("x_for_you", "github", "papers", "articles", "hackernews"):
        (phase / f"{name}.jsonl").write_text("")
    (phase / "index.json").write_text(
        json.dumps({"total_items": 2, "files": {"x_list": 2}, "item_ids": ["x_list:1", "x_list:2"]})
    )
    affected = _rewrite_x_handoffs(runtime, ["1"])
    assert affected == {"run-x"}
    rows = [json.loads(line) for line in (phase / "x_list.jsonl").read_text().splitlines()]
    assert [row["item_id"] for row in rows] == ["x_list:2"]
    index = json.loads((phase / "index.json").read_text())
    assert index["total_items"] == 1
    assert index["item_ids"] == ["x_list:2"]


class _Response:
    def __init__(self, data=None, text=""):
        self._data = data or {}
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _HTTPClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json):
        return _Response({"data": {"id": "job-1", "upload_url": "https://upload"}})

    async def put(self, url, content, headers):
        assert content == b"1\n2\n"
        return _Response()

    async def get(self, url):
        if url == "https://download":
            return _Response(text='{"id":"2","reason":"deleted"}\n')
        return _Response({"data": {"status": "complete", "download_url": "https://download"}})


@pytest.mark.asyncio
async def test_batch_compliance_upload_poll_and_download(tmp_path, monkeypatch):
    runner = XComplianceRunner(
        RuntimeConfig(runtime_root=tmp_path / "runtime"), SourcesConfig()
    )
    monkeypatch.setattr("ai_digest.x_compliance.XTokenStore.load_bearer", lambda self: "bearer")
    monkeypatch.setattr("ai_digest.x_compliance.httpx.AsyncClient", _HTTPClient)
    assert await runner._batch_events(["1", "2"]) == {"2": "deleted"}

    monkeypatch.setattr("ai_digest.x_compliance.XTokenStore.load_bearer", lambda self: None)
    with pytest.raises(RuntimeError, match="bearer token"):
        await runner._batch_events(["1"])


@pytest.mark.asyncio
async def test_compliance_run_and_apply_events(tmp_path, monkeypatch):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime",
        shared_runtime_root=tmp_path / "runtime" / "queue",
    )
    runner = XComplianceRunner(runtime, SourcesConfig())
    await runner.state.init()
    item = SourceItem(
        item_id="x_list:1", item_type="x_post", source="x_list", surface="x", payload={"post_id": "1"}
    )

    class FakeState:
        async def init(self):
            return None

        async def active_x_post_ids(self):
            return ["1", "2"]

        async def expired_x_post_ids(self):
            return ["1"]

        async def mark_x_verified(self, ids):
            assert ids == []

        async def x_dependencies_for_posts(self, ids):
            return [{"run_id": "run-a"}]

        async def pop_x_posts(self, ids):
            return [item]

        async def mark_x_compliance(self, ids, event, details):
            assert details["post_count"] == len(ids)

    removed = []
    runner.state = FakeState()
    runner.store = SimpleNamespace(remove_item_content=removed.append)

    async def fake_batch(ids):
        return {"2": "deleted"}

    rebuilt = []

    async def fake_rebuild(runtime_config, run_dir, run_id):
        rebuilt.append((run_dir, run_id))

    monkeypatch.setattr(runner, "_batch_events", fake_batch)
    monkeypatch.setattr("ai_digest.x_compliance._rewrite_x_handoffs", lambda runtime, ids: {"run-b"})
    (tmp_path / "run-a").mkdir()
    (tmp_path / "run-b").mkdir()
    monkeypatch.setattr("ai_digest.x_compliance._run_dir", lambda runtime, run_id: tmp_path / run_id)
    monkeypatch.setattr("ai_digest.x_compliance._rebuild_run", fake_rebuild)
    result = await runner.run()
    assert result == {
        "checked_posts": 2,
        "expired_posts": 1,
        "compliance_events": 2,
        "purged_posts": 2,
        "rebuilt_runs": 2,
    }
    assert removed == [item]
    assert [value[1] for value in rebuilt] == ["run-a", "run-b"]


def test_compliance_helpers(tmp_path):
    assert _group_events({"1": "deleted", "2": "deleted", "3": "protected"}) == {
        "deleted": ["1", "2"],
        "protected": ["3"],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "00_run_manifest.json").write_text('{"run_id":"run-1"}')
    (run_dir / "good" / "codex.json").parent.mkdir()
    (run_dir / "good" / "codex.json").write_text('{"thread_id":"thread-1"}')
    (run_dir / "bad" / "codex.json").parent.mkdir()
    (run_dir / "bad" / "codex.json").write_text("bad")
    assert _thread_ids(run_dir) == ["thread-1"]
    assert _manifest_run_id(run_dir) == "run-1"
    assert _row_post_id({"payload": {"post_id": "42"}}) == "42"
    assert _row_post_id({"payload": "bad"}) == ""

    runtime = RuntimeConfig(runtime_root=tmp_path / "runtime")
    runtime.runtime_root.mkdir()
    with sqlite3.connect(runtime.runtime_root / "state.db") as connection:
        connection.execute("CREATE TABLE runs (run_id TEXT, path TEXT)")
        connection.execute("INSERT INTO runs VALUES (?, ?)", ("run-1", str(run_dir)))
    assert _run_dir(runtime, "run-1") == run_dir
    assert _run_dir(runtime, "missing") is None

    manifest = SimpleNamespace(phases={"a": "success"})
    assert str(_overall_status(manifest)) == "success"
    manifest.phases["b"] = "quiet"
    assert str(_overall_status(manifest)) == "quiet"
    manifest.phases["c"] = "partial"
    assert str(_overall_status(manifest)) == "partial"
    manifest.phases["d"] = "failed"
    assert str(_overall_status(manifest)) == "failed"


@pytest.mark.asyncio
async def test_rebuild_removes_old_outputs_and_republishes(tmp_path, monkeypatch):
    runtime = RuntimeConfig(runtime_root=tmp_path / "runtime")
    run_dir = runtime.runtime_root / "runs" / "2026-08-30" / "attempt-0001"
    (run_dir / "01_phase1").mkdir(parents=True)
    for name in ("02_routing", "03_research", "04_brief"):
        (run_dir / name).mkdir()
    (run_dir / "02_routing" / "codex.json").write_text('{"thread_id":"old-thread"}')
    (run_dir / "00_run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "date": "2026-08-30",
                "attempt": 1,
                "timezone": "Asia/Shanghai",
                "window_start": "2026-08-29T23:00:00Z",
                "window_end": "2026-08-30T23:00:00Z",
                "phases": {"phase1": "success", "phase2": "success"},
            }
        )
    )
    publish = run_dir / "05_publish"
    publish.mkdir()
    (publish / "publish_manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "nodes": {
                    "day": {
                        "key": "day",
                        "title": "Day",
                        "node_token": "node-1",
                        "obj_token": "doc-1",
                    }
                },
            }
        )
    )
    deleted_nodes = []
    published = []

    class FakePublisher:
        def __init__(self, config):
            self.cli = SimpleNamespace(delete_node=deleted_nodes.append)

        def publish(self, directory, marker):
            published.append((directory, marker))

    class FakePhases:
        def __init__(self, config):
            pass

        async def route(self, directory):
            (directory / "02_routing").mkdir()
            return RoutingOutput(bundles=[], assignments=[], quiet_reason="quiet")

        async def research(self, directory, routing):
            (directory / "03_research").mkdir()
            (directory / "03_research" / "failures.json").write_text("[]")
            return []

        async def brief(self, directory, routing, successes):
            (directory / "04_brief").mkdir()

    deleted_threads = []
    monkeypatch.setattr("ai_digest.x_compliance.LarkPublisher", FakePublisher)
    monkeypatch.setattr("ai_digest.x_compliance.AgentPhases", FakePhases)
    monkeypatch.setattr(
        "ai_digest.x_compliance.subprocess.run",
        lambda command, **kwargs: deleted_threads.append(command),
    )
    indexed = []
    monkeypatch.setattr(
        "ai_digest.pipeline._replace_x_dependencies_sync",
        lambda config, directory, run_id: indexed.append(run_id),
    )
    await _rebuild_run(runtime, run_dir, "run-1")
    assert [node.node_token for node in deleted_nodes] == ["node-1"]
    assert deleted_threads[0][-1] == "old-thread"
    assert published == [(run_dir, "COMPLIANCE_UPDATED")]
    assert indexed == ["run-1"]
    manifest = json.loads((run_dir / "00_run_manifest.json").read_text())
    assert manifest["phases"]["phase5"] == "success"
