from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_digest.collectors.articles import ArticleCollector
from ai_digest.collectors.arxiv import ArxivCollector
from ai_digest.collectors.x_for_you import _post_id
from ai_digest.collectors.x_list import XListCollector
from ai_digest.models import TimeBasis
from ai_digest.store import FileStore, StateDB


@pytest.fixture
def store_state(tmp_path):
    return FileStore(tmp_path), StateDB(tmp_path / "state.db")


def test_x_list_item_uses_official_occurrence_time(store_state):
    store, state = store_state
    collector = XListCollector({"retention_days": 30}, store, state)
    now = datetime(2026, 8, 30, tzinfo=UTC)
    item = collector._to_item(
        {
            "id": "123",
            "text": "hello",
            "author_id": "9",
            "created_at": "2026-08-29T23:00:00Z",
            "public_metrics": {"like_count": 1},
            "referenced_tweets": [{"type": "quoted", "id": "456"}],
        },
        {"9": {"username": "user"}},
        {"456": {"text": "quote"}},
        "sha256:" + "a" * 64 + ".json",
        now,
    )
    assert item.item_id == "x_list:123"
    assert item.time_basis == TimeBasis.OCCURRED
    assert item.payload["references"][0]["text"] == "quote"


def test_arxiv_version_is_source_native(store_state):
    store, state = store_state
    collector = ArxivCollector({}, store, state)
    item = collector._entry(
        {
            "link": "https://arxiv.org/abs/2608.12345v2",
            "title": " A paper ",
            "summary": " abstract ",
            "published": "2026-08-29T00:00:00Z",
            "updated": "2026-08-30T00:00:00Z",
            "authors": [{"name": "A"}],
            "tags": [{"term": "cs.RO"}],
        },
        "sha256:" + "b" * 64 + ".xml",
        datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert item is not None
    assert item.item_id == "arxiv:2608.12345:v2"
    assert item.change == "version"
    assert item.time_basis == TimeBasis.UPDATED


def test_article_discovery_parsers_are_source_specific(store_state):
    store, state = store_state
    collector = ArticleCollector([], store, state)
    index = '<a href="/news/one">One</a><a href="https://elsewhere.test/x">X</a>'
    rows = collector._index_rows(
        index,
        {
            "url": "https://example.com/news",
            "path_contains": "/news/",
            "allowed_domains": ["example.com"],
        },
    )
    assert [row["url"] for row in rows] == ["https://example.com/news/one"]


def test_post_id_parser():
    assert _post_id("/user/status/123?x=1") == "123"
    assert _post_id("/home") is None
