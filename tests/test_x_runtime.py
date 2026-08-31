from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_digest.collectors.x_list import XListCollector, _x_datetime
from ai_digest.models import HealthStatus, TimeBasis
from ai_digest.store import FileStore, StateDB


class Response:
    text = "{}"
    headers = {"content-type": "application/json"}
    status_code = 200
    url = "https://api.twitterapi.io/twitter/list/tweets"

    def __init__(self, payload):  # type: ignore[no-untyped-def]
        self.payload = payload

    def json(self):  # type: ignore[no-untyped-def]
        return self.payload


def tweet(post_id: str, text: str = "post") -> dict:
    return {
        "id": post_id,
        "text": text,
        "url": f"https://x.com/user/status/{post_id}",
        "createdAt": "Sat Aug 30 00:00:00 +0000 2026",
        "author": {"id": "9", "userName": "user", "name": "User"},
        "entities": {"urls": [{"expanded_url": "https://example.com/source"}]},
        "quoted_tweet": {
            "id": "99",
            "text": "quoted",
            "author": {"id": "8", "userName": "quoted"},
        },
    }


@pytest.mark.asyncio
async def test_multi_list_incremental_pagination_and_global_dedup(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()
    calls = []

    async def fake_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        params = kwargs["params"]
        calls.append(params.copy())
        if params["listId"] == "a" and not params.get("cursor"):
            return Response(
                {"tweets": [tweet("1"), tweet("2")], "has_next_page": True, "next_cursor": "a2"}
            )
        if params["listId"] == "a":
            return Response({"tweets": [tweet("3")], "has_next_page": False})
        return Response({"tweets": [tweet("2"), tweet("4")], "has_next_page": False})

    monkeypatch.setattr("ai_digest.collectors.x_list.TwitterApiIOKeyStore.load", lambda self: "k")
    monkeypatch.setattr("ai_digest.collectors.x_list.SafeHTTPClient.request", fake_request)
    collector = XListCollector(
        {
            "enabled": True,
            "list_ids": ["a", "b"],
            "retention_days": 30,
            "cursor_overlap_seconds": 300,
        },
        store,
        state,
    )
    now = datetime(2026, 8, 30, 1, tzinfo=UTC)
    result = await collector.collect(now)
    assert result.health.status == HealthStatus.SUCCESS
    assert result.health.fetched_count == 5
    assert result.health.new_count == 4
    assert {item.item_id for item in result.items} == {
        "x_list:1",
        "x_list:2",
        "x_list:3",
        "x_list:4",
    }
    duplicate = next(item for item in result.items if item.item_id == "x_list:2")
    assert duplicate.payload["list_ids"] == ["a", "b"]
    assert duplicate.payload["references"][0]["text"] == "quoted"
    assert duplicate.payload["expanded_links"] == ["https://example.com/source"]
    assert duplicate.time_basis == TimeBasis.OCCURRED
    assert calls[1]["cursor"] == "a2"
    expected_cursor = str(int(now.timestamp()) - 300)
    assert await state.get_cursor("x_list:a:since_time") == expected_cursor
    assert await state.get_cursor("x_list:b:since_time") == expected_cursor
    assert result.health.surfaces["a"]["pages"] == 2


@pytest.mark.asyncio
async def test_failed_list_does_not_advance_its_cursor(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()

    async def fake_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs["params"]["listId"] == "bad":
            raise RuntimeError("provider down")
        return Response({"tweets": [tweet("1")], "has_next_page": False})

    monkeypatch.setattr("ai_digest.collectors.x_list.TwitterApiIOKeyStore.load", lambda self: "k")
    monkeypatch.setattr("ai_digest.collectors.x_list.SafeHTTPClient.request", fake_request)
    collector = XListCollector(
        {"enabled": True, "list_ids": ["good", "bad"]}, store, state
    )
    result = await collector.collect(datetime(2026, 8, 30, 1, tzinfo=UTC))
    assert result.health.status == HealthStatus.PARTIAL
    assert await state.get_cursor("x_list:good:since_time") is not None
    assert await state.get_cursor("x_list:bad:since_time") is None
    assert result.health.surfaces["bad"]["status"] == "failed"


@pytest.mark.asyncio
async def test_page_cap_is_partial_and_cursor_is_not_advanced(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    state = StateDB(tmp_path / "state.db")
    await state.init()

    async def fake_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        return Response(
            {"tweets": [tweet("1")], "has_next_page": True, "next_cursor": "more"}
        )

    monkeypatch.setattr("ai_digest.collectors.x_list.TwitterApiIOKeyStore.load", lambda self: "k")
    monkeypatch.setattr("ai_digest.collectors.x_list.SafeHTTPClient.request", fake_request)
    collector = XListCollector(
        {"enabled": True, "list_ids": ["a"], "max_pages_per_list": 1}, store, state
    )
    result = await collector.collect(datetime(2026, 8, 30, 1, tzinfo=UTC))
    assert result.health.status == HealthStatus.PARTIAL
    assert await state.get_cursor("x_list:a:since_time") is None
    assert "page cap reached" in result.health.errors[0]


def test_twitter_datetime_parses_provider_format():
    assert _x_datetime("Sat Aug 30 00:00:00 +0000 2026") == datetime(
        2026, 8, 30, tzinfo=UTC
    )
