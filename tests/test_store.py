from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from ai_digest.models import FetchManifest, SourceItem
from ai_digest.store import FileStore, StateDB, load_jsonl, source_group, x_expiry
from ai_digest.utils import atomic_write_jsonl


@pytest.mark.asyncio
async def test_store_deduplicates_and_delivers_atomically(tmp_path):
    files = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime.now(UTC)
    item = SourceItem(
        item_id="x:1",
        item_type="x_post",
        source="x_list",
        surface="private_list",
        handoff_at=now,
        first_observed_at=now,
        payload={"text": "hello"},
    )
    assert await state.put_items([item]) == ["x:1"]
    assert await state.put_items([item]) == []
    pending = await state.pending_items(now - timedelta(hours=1), now + timedelta(hours=1))
    assert [row.item_id for row in pending] == ["x:1"]
    await state.mark_delivered(["x:1"], "run-1")
    assert await state.pending_items(now - timedelta(hours=1), now + timedelta(hours=1)) == []

    first = files.write_blob("same", ".txt")
    second = files.write_blob("same", ".txt")
    assert first == second
    assert files.resolve_blob(first).read_text() == "same"


@pytest.mark.asyncio
async def test_state_cursor_baseline_and_expiry(tmp_path):
    state = StateDB(tmp_path / "state.db")
    await state.init()
    assert await state.get_cursor("a") is None
    await state.set_cursor("a", "cursor")
    assert await state.get_cursor("a") == "cursor"
    assert await state.baseline("a") is None
    await state.set_baseline("a", 12)
    assert await state.baseline("a") == 12

    now = datetime.now(UTC)
    expired = SourceItem(
        item_id="expired",
        item_type="x_post",
        source="x_list",
        surface="private_list",
        handoff_at=now,
        first_observed_at=now,
        expires_at=now - timedelta(seconds=1),
    )
    await state.put_items([expired])
    rows = await state.pop_expired_x_items(now)
    assert [row.item_id for row in rows] == ["expired"]

    current = expired.model_copy(
        update={"item_id": "x_list:123", "expires_at": now + timedelta(days=1)}
    )
    await state.put_items([current])
    assert [row.item_id for row in await state.pop_x_post("123")] == ["x_list:123"]


@pytest.mark.asyncio
async def test_sealed_run_is_recoverable_and_delivery_waits_for_queue_visibility(tmp_path):
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime.now(UTC)
    run_dir = tmp_path / "runs" / "2026-08-30" / "attempt-0001"
    item = SourceItem(
        item_id="hn:sealed",
        item_type="hn_story",
        source="hackernews",
        surface="top",
        handoff_at=now,
        first_observed_at=now,
    )
    await state.put_items([item])
    await state.record_run("run-sealed", "2026-08-30", 1, "running", run_dir)
    await state.seal_run("run-sealed", "success", [item.item_id])

    assert await state.list_sealed_unqueued_runs() == [("run-sealed", run_dir)]
    assert await state.pending_items(now - timedelta(hours=1), now + timedelta(hours=1)) == []
    async with aiosqlite.connect(state.path) as db:
        cursor = await db.execute(
            "SELECT delivered_run_id, sealed_run_id FROM source_items WHERE item_id = ?",
            (item.item_id,),
        )
        assert await cursor.fetchone() == (None, "run-sealed")

    assert await state.mark_run_queued("run-sealed") is True
    assert await state.mark_run_queued("run-sealed") is False
    assert await state.list_sealed_unqueued_runs() == []
    async with aiosqlite.connect(state.path) as db:
        cursor = await db.execute(
            "SELECT delivered_run_id FROM source_items WHERE item_id = ?",
            (item.item_id,),
        )
        assert await cursor.fetchone() == ("run-sealed",)


@pytest.mark.asyncio
async def test_local_completion_atomically_delivers_sealed_run(tmp_path):
    state = StateDB(tmp_path / "state.db")
    await state.init()
    now = datetime.now(UTC)
    item = SourceItem(
        item_id="hn:local",
        item_type="hn_story",
        source="hackernews",
        surface="top",
        handoff_at=now,
        first_observed_at=now,
    )
    await state.put_items([item])
    await state.record_run("run-local", "2026-08-30", 1, "running", tmp_path / "run")
    await state.seal_run("run-local", "success", [item.item_id])

    assert await state.mark_run_locally_completed("run-local", "published") is True
    assert await state.mark_run_locally_completed("run-local", "published") is False
    assert await state.list_sealed_unqueued_runs() == []
    async with aiosqlite.connect(state.path) as db:
        cursor = await db.execute(
            """
            SELECT source_items.delivered_run_id, runs.handoff_state
            FROM source_items CROSS JOIN runs
            WHERE source_items.item_id = ? AND runs.run_id = ?
            """,
            (item.item_id, "run-local"),
        )
        assert await cursor.fetchone() == ("run-local", "published")


@pytest.mark.asyncio
async def test_daily_run_gate_allows_failed_and_skipped_retries(tmp_path):
    state = StateDB(tmp_path / "state.db")
    await state.init()
    date = "2026-08-30"
    await state.record_run("failed", date, 1, "failed", Path("/tmp/failed"))
    await state.record_run("skipped", date, 2, "skipped_asleep", Path("/tmp/skipped"))
    assert await state.has_daily_run_in_progress_or_done(date) is False

    await state.record_run("active", date, 3, "running", Path("/tmp/active"))
    assert await state.has_daily_run_in_progress_or_done(date) is True

    stale_now = datetime.now(UTC) + timedelta(minutes=19)
    assert await state.has_daily_run_in_progress_or_done(date, now=stale_now) is False


@pytest.mark.asyncio
async def test_state_init_migrates_existing_phase1_schema(tmp_path):
    path = tmp_path / "state.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE source_items (
                item_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                surface TEXT NOT NULL,
                item_type TEXT NOT NULL,
                handoff_at TEXT NOT NULL,
                first_observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                delivered_run_id TEXT,
                expires_at TEXT
            );
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await db.commit()

    state = StateDB(path)
    await state.init()
    async with aiosqlite.connect(path) as db:
        source_columns = await (await db.execute("PRAGMA table_info(source_items)")).fetchall()
        run_columns = await (await db.execute("PRAGMA table_info(runs)")).fetchall()
        table = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_items'"
        )
        assert await table.fetchone() == (1,)
    assert "sealed_run_id" in {row[1] for row in source_columns}
    assert {"handoff_state", "queued_at"} <= {row[1] for row in run_columns}


def test_attempt_directories_are_monotonic(tmp_path):
    store = FileStore(tmp_path)
    first, first_path = store.next_attempt_dir("2026-08-30")
    second, second_path = store.next_attempt_dir("2026-08-30")
    assert (first, second) == (1, 2)
    assert first_path.name == "attempt-0001"
    assert second_path.name == "attempt-0002"


def test_revision_manifest_helpers_and_grouping(tmp_path):
    store = FileStore(tmp_path)
    now = datetime.now(UTC)
    item = SourceItem(
        item_id="article:1",
        item_type="article",
        source="article:test",
        surface="primary",
        raw_refs=[store.write_blob("article")],
    )
    assert store.write_revision(item).exists()
    manifest = FetchManifest(
        fetch_id="f1",
        source="test",
        started_at=now,
        completed_at=now,
        request={"url": "https://example.com"},
    )
    assert store.write_fetch_manifest(manifest).exists()
    path = tmp_path / "rows.jsonl"
    atomic_write_jsonl(path, [{"a": 1}])
    assert load_jsonl(path) == [{"a": 1}]
    assert load_jsonl(tmp_path / "missing") == []
    assert source_group(item) == "articles"
    assert x_expiry(now, 30) == now + timedelta(days=30)
