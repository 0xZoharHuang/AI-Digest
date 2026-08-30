from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_digest.collectors.articles import ArticleCollector
from ai_digest.collectors.arxiv import ArxivCollector
from ai_digest.collectors.x_for_you import XForYouCollector, _post_id
from ai_digest.models import FetchManifest, SourceItem, TimeBasis
from ai_digest.store import FileStore, StateDB


@pytest.fixture
def store_state(tmp_path):
    return FileStore(tmp_path), StateDB(tmp_path / "state.db")


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


def test_arxiv_withdrawal_without_version_is_a_distinct_tombstone(store_state):
    store, state = store_state
    collector = ArxivCollector({}, store, state)
    item = collector._entry(
        {
            "link": "https://arxiv.org/abs/2608.12345",
            "title": "Withdrawn paper",
            "summary": "withdrawn",
            "published": "2026-08-20T00:00:00Z",
            "updated": "2026-08-30T00:00:00Z",
            "arxiv_announce_type": "withdraw",
        },
        "sha256:" + "c" * 64 + ".xml",
        datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert item is not None
    assert item.item_id == "arxiv:2608.12345:withdraw:20260830T000000"
    assert item.change == "withdrawn"
    assert item.content_status.value == "tombstone"
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


def test_a16z_index_selector_keeps_current_post_cards_not_navigation(store_state):
    store, state = store_state
    collector = ArticleCollector([], store, state)
    html = """
    <a href="/ai/" class="menu-flyout__level-two-link">AI category</a>
    <a href="https://a16z.com/the-machine-age-fund/"
       class="transition-colors hover:text-[--post-title-hover,#336d5d]">Machine Age</a>
    <a href="/can-agents-use-a-computer-yet-weve-got-the-data/"
       class="transition-colors hover:text-[--post-title-hover,#336d5d]">Agents</a>
    """
    rows = collector._index_rows(
        html,
        {
            "url": "https://a16z.com/news-content/",
            "link_selector": "a[href][class*='--post-title-hover']",
            "allowed_domains": ["a16z.com"],
        },
    )
    assert [row["url"] for row in rows] == [
        "https://a16z.com/the-machine-age-fund/",
        "https://a16z.com/can-agents-use-a-computer-yet-weve-got-the-data/",
    ]


@pytest.mark.asyncio
async def test_article_content_selector_excludes_site_disclaimer(store_state):
    store, state = store_state
    await state.init()
    collector = ArticleCollector([], store, state)
    now = datetime(2026, 8, 30, tzinfo=UTC)

    class Response:
        text = """
        <html><body>
          <div class="js-article-content"><h1>Machine Age</h1><p>Actual thesis.</p></div>
          <footer><p>Views expressed are not investment advice.</p></footer>
        </body></html>
        """
        url = "https://a16z.com/the-machine-age-fund/"

    class Client:
        async def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return Response()

    item, _updates = await collector._article(
        Client(),
        {
            "id": "a16z",
            "role": "editorial",
            "kind": "index",
            "content_selector": ".js-article-content",
            "allowed_domains": ["a16z.com"],
        },
        {
            "url": "https://a16z.com/the-machine-age-fund/",
            "title": "Machine Age",
            "summary": "",
        },
        now,
    )
    assert item is not None
    assert "Actual thesis" in item.payload["text_preview"]
    assert "investment advice" not in item.payload["text_preview"]


def test_sitemap_sorts_newest_before_applying_limit(store_state):
    store, state = store_state
    collector = ArticleCollector([], store, state)
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/research/old</loc><lastmod>2026-08-25</lastmod></url>
      <url><loc>https://example.com/research/new</loc><lastmod>2026-08-29</lastmod></url>
      <url><loc>https://example.com/research/mid</loc><lastmod>2026-08-27</lastmod></url>
    </urlset>"""
    rows = collector._sitemap_rows(
        xml,
        {"path_contains": "/research/", "max_entries": 2},
        datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert [row["url"] for row in rows] == [
        "https://example.com/research/new",
        "https://example.com/research/mid",
    ]


@pytest.mark.asyncio
async def test_article_source_zero_discovery_is_not_silent(store_state, monkeypatch):
    store, state = store_state
    collector = ArticleCollector(
        [{"id": "changed-site", "kind": "index", "url": "https://example.com/news"}],
        store,
        state,
    )

    class Response:
        text = "<html><body>No matching links</body></html>"
        content = text.encode()
        headers = {"content-type": "text/html"}
        status_code = 200
        url = "https://example.com/news"

    async def fake_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        return Response()

    monkeypatch.setattr("ai_digest.collectors.articles.SafeHTTPClient.request", fake_request)
    result = await collector.collect(datetime(2026, 8, 30, tzinfo=UTC))
    assert result.health.status.value == "failed"
    assert "discovery returned zero rows" in result.health.errors[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_stage", ["put_items", "manifest"])
async def test_article_cursors_advance_only_after_all_durable_writes(
    store_state, monkeypatch, fail_stage
):
    store, state = store_state
    await state.init()
    now = datetime(2026, 8, 30, tzinfo=UTC)
    collector = ArticleCollector(
        [{"id": "test", "url": "https://example.com/feed"}],
        store,
        state,
    )
    item = SourceItem(
        item_id="article:test:one:hash",
        item_type="article",
        source="article:test",
        surface="editorial",
        handoff_at=now,
        first_observed_at=now,
    )
    manifest = FetchManifest(
        fetch_id="fetch-1",
        source="articles:test",
        started_at=now,
        completed_at=now,
        request={"url": "https://example.com/feed"},
    )

    async def fake_discover(client, config, observed_at):
        return [{"url": "https://example.com/one"}], manifest

    async def fake_article(client, config, row, observed_at):
        return item, {"article:test:cursor": "next"}

    monkeypatch.setattr(collector, "_discover", fake_discover)
    monkeypatch.setattr(collector, "_article", fake_article)
    if fail_stage == "put_items":

        async def fail_put(items):
            raise RuntimeError("database write failed")

        monkeypatch.setattr(state, "put_items", fail_put)
    else:

        def fail_manifest(value):
            raise RuntimeError("manifest write failed")

        monkeypatch.setattr(store, "write_fetch_manifest", fail_manifest)

    with pytest.raises(RuntimeError):
        await collector.collect(now)
    assert await state.get_cursor("article:test:cursor") is None


def test_post_id_parser():
    assert _post_id("/user/status/123?x=1") == "123"
    assert _post_id("/home") is None


@pytest.mark.asyncio
async def test_for_you_explicitly_selects_and_verifies_tab(store_state):
    store, state = store_state
    collector = XForYouCollector({}, store, state)

    class Page:
        calls = 0
        waited = 0

        async def evaluate(self, script):  # type: ignore[no-untyped-def]
            self.calls += 1
            return "clicked" if self.calls == 1 else True

        async def wait_for_timeout(self, milliseconds):  # type: ignore[no-untyped-def]
            self.waited = milliseconds

    page = Page()
    await collector._select_for_you(page)  # type: ignore[arg-type]
    assert page.calls == 2
    assert page.waited == 1500


@pytest.mark.asyncio
async def test_for_you_enters_cooldown_after_repeated_failures(store_state):
    store, state = store_state
    await state.init()
    collector = XForYouCollector(
        {"max_consecutive_failures": 2, "cooldown_hours": 6}, store, state
    )
    now = datetime(2026, 8, 30, tzinfo=UTC)
    await collector._record_failure(now)
    assert await state.get_cursor("x_for_you:cooldown_until") is None
    await collector._record_failure(now)
    assert await state.get_cursor("x_for_you:cooldown_until") == "2026-08-30T06:00:00+00:00"
