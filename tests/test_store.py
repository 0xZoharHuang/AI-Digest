from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
