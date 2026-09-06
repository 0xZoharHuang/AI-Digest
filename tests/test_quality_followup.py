import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ai_digest.codex_runner import CodexResult
from ai_digest.collectors.articles import ArticleCollector, article_error_kind
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.models import ResearchPackage, SourceItem
from ai_digest.phase2_attention import build_phase2_unit_documents
from ai_digest.phase2_labels import (
    SemanticPhase2,
    constrained_components,
    research_eligibility,
    validate_artifacts,
    validate_identities,
)
from ai_digest.phase3_admission import explore
from ai_digest.run_counts import count_sentence, run_counts
from ai_digest.utils import atomic_write_json, atomic_write_jsonl
from ai_digest.v3 import append_run_status, build_observation_units, select_phase3_admission


def test_tombstones_are_not_weak_signals_and_old_content_survives():
    empty = {"observations": [{"content_status": "tombstone", "payload": {}}]}
    assert research_eligibility(empty) == "no_readable_content"
    for payload in ({"title": "paper"}, {"references": [{"text": "actual evidence"}]}):
        assert research_eligibility({"observations": [{"content_status": "tombstone", "payload": payload}]}) == "eligible"
    for status in ("full", "preview", "extraction_failed"):
        assert research_eligibility({"observations": [{"content_status": status, "payload": {"url": "https://example.org"}}]}) == "eligible"


def test_singleton_abstention_is_defensive_not_a_production_causal_claim():
    assert validate_identities({"a": "a", "b": "b"}, {"a", "b"}) == []
    assert constrained_components(["a", "b"], [[["a"], ["b"]], [["a", "b"]]], [])[0] == [["a", "b"]]
    with pytest.raises(ValueError, match="chains or cycles"):
        validate_identities({"a": "b", "b": "c", "c": "c"}, {"a", "b", "c"})
    with pytest.raises(ValueError, match="chains or cycles"):
        validate_identities({"a": "b", "b": "a"}, {"a", "b"})


def test_exploration_is_replayable_balanced_and_keeps_weak_signals():
    rows = [{"object_id": f"{s}{i}", "sources": [s], "unit_count": 1,
             "readable": True, "signals": {"unclear": 1}} for s in ("hn", "github", "x") for i in range(10)]
    rows += [{"object_id": "empty", "unit_count": 1, "readable": False},
             {"object_id": "large", "unit_count": 4, "readable": True}]
    selected, strata = explore(rows, {"hn0"}, 3, "day-input-version")
    assert len(selected) == len(set(selected)) == 3
    assert strata == {"hn": 1, "github": 1, "x": 1}
    assert not {"empty", "large", "hn0"} & set(selected)
    assert explore(list(reversed(rows)), {"hn0"}, 3, "day-input-version") == (selected, strata)
    assert explore(rows, set(), 0, "seed") == ([], {})
    assert len(explore(rows, set(), 100, "seed")[0]) == 30


def test_counts_do_not_confuse_information_and_packages(tmp_path):
    routing = tmp_path / "02_routing"
    atomic_write_jsonl(routing / "units.jsonl", [{"unit_id": str(i), "observations": [{"text": "real\u2028text"}]} for i in range(8)])
    atomic_write_json(routing / "packages.json", [
        {"package_id": "a", "unit_ids": ["0", "1", "2"]}, {"package_id": "b", "unit_ids": ["3"]}])
    atomic_write_json(tmp_path / "03_research" / "phase3_admission.json", {"selected_object_ids": ["a"]})
    atomic_write_json(tmp_path / "03_research" / "a" / "research_manifest.json", {"reviewed_unit_ids": ["0", "1", "2"]})
    counts = run_counts(tmp_path)
    assert counts["packages"] == 2 and counts["information"] == 8
    assert counts["scheduled_information"] == counts["reviewed_information"] == 3
    assert "2 个候选信息包" in count_sentence(tmp_path)
    atomic_write_json(tmp_path / "01_phase1/source_health.json", {})
    report = tmp_path / "04_brief/daily_brief.md"
    report.parent.mkdir(parents=True)
    report.write_text("# 导航\n\n今日发现 999 条候选信息。\n\n## 研究报告\n\n### 检索系统\n\n研究如何检查候选信息。\n\n[阅读](report://a)")
    append_run_status(report, tmp_path, {"a": "a/main_report.md"})
    content = report.read_text()
    assert "999" not in content and "研究如何检查候选信息" in content
    assert "[阅读](report://a)（输入信息：3 条）" in content


def test_counts_support_historical_objects_and_legacy_packages(tmp_path):
    routing = tmp_path / "02_routing"
    atomic_write_jsonl(routing / "units.jsonl", [{"unit_id": "u", "item_ids": ["one", "two"]}])
    atomic_write_json(routing / "objects.json", [{"object_id": "a", "unit_ids": ["u"]}])
    admission = tmp_path / "03_research/phase3_admission.json"
    atomic_write_json(admission, {"selected_object_ids": ["a"]})
    counts = run_counts(tmp_path)
    assert counts["observations"] == 2 and counts["scheduled_information"] == 1
    assert counts["packages"] == 1 and counts["unscheduled_packages"] == 0
    atomic_write_json(routing / "packages.json", [{"package_id": "a", "investigate_unit_ids": ["u"], "supporting_unit_ids": ["u"]}])
    assert run_counts(tmp_path) == counts
    atomic_write_json(admission, {"selected_object_ids": ["missing"]})
    with pytest.raises(ValueError, match="missing packages"):
        run_counts(tmp_path)


@pytest.mark.asyncio
async def test_article_retry_is_durable_and_recovers():
    class State:
        values = {}
        async def get_cursor(self, key):
            return self.values.get(key)
    class Store:
        def write_blob(self, *args):
            return "sha256:test.txt"
    class Client:
        calls = 0
        success = False
        async def request(self, method, url, **kwargs):
            assert kwargs.get("validate_public_url") is True
            self.calls += 1
            response = httpx.Response(200 if self.success else 403,
                text="<article>A useful body.</article>", request=httpx.Request(method, url))
            response.raise_for_status()
            return response
    state, client = State(), Client()
    collector = ArticleCollector([], Store(), state)
    now = datetime(2026, 9, 6, tzinfo=UTC)
    config = {"id": "site", "kind": "rss", "content_selector": "article"}
    row = {"url": "https://example.org/post", "title": "post", "occurred_at": now}
    item, updates = await collector._article(client, config, row, now)
    assert item.payload["extraction_error_kind"] == "access_denied"
    state.values.update(updates)
    retry_key = next(iter(updates))
    assert json.loads(updates[retry_key])["next_retry_at"] == (now + timedelta(hours=1)).isoformat()
    assert await collector._article(client, config, row, now + timedelta(minutes=30)) == (None, {})
    assert client.calls == 1 and collector.deferred_bodies == {"site": 1}
    _, updates = await collector._article(client, config, row, now + timedelta(hours=1))
    state.values.update(updates)
    assert json.loads(updates[retry_key])["next_retry_at"] == (now + timedelta(hours=7)).isoformat()
    client.success = True
    item, updates = await collector._article(client, config, row, now + timedelta(hours=7))
    assert item.payload["extraction_error"] is None and updates[retry_key] is None
    # Old metadata-only cursors and a malformed retry count cannot turn another
    # failed fetch into a successful recovery or crash the entire source.
    client.success = False
    state.values[retry_key] = json.dumps({"failures": "invalid"})
    state.values[retry_key.replace("article-retry:", "article:")] = "metadata"
    item, updates = await collector._article(client, config, row, now + timedelta(hours=8))
    assert item.payload["extraction_error_kind"] == "access_denied"
    assert json.loads(updates[retry_key])["failures"] == 1


def test_error_categories():
    assert article_error_kind(httpx.ReadTimeout("timeout")) == "timeout"
    assert article_error_kind(ValueError("empty article body")) == "empty_body"
    assert article_error_kind(ValueError("content selector did not match")) == "selector_mismatch"


def test_named_subjects_do_not_transitively_bridge_and_preserve_repo_identity():
    from ai_digest.phase2_subjects import subject_assignments, subject_components
    assert subject_assignments({"r1": "MODEL: Astra", "r2": "r2"}, {"r1": "a", "r2": "b"}) == {
        "a": "object:astra", "b": "unit:b"}
    assert subject_assignments({"r1": "product:Oura Ring"}, {"r1": "a"}) == {"a": "object:oura ring"}
    assert subject_assignments({"r1": "company:OpenAI"}, {"r1": "a"}) == {"a": "unit:a"}
    assert subject_assignments({"r1": "repo:ambiguous"}, {"r1": "a"}) == {"a": "unit:a"}
    docs = {k: {"observations": []} for k in ("a", "b", "c", "d", "e")}
    docs["d"] = {"observations": [{"item_type": "github_repository", "payload": {"full_name": "Owner/A"}}]}
    docs["e"] = {"observations": [{"item_type": "github_repository", "payload": {"full_name": "Other/A"}}]}
    groups, keys = subject_components(list(docs), [
        {"a": "model:astra", "b": "model:astra", "c": "model:muse", "d": "repo:same/name", "e": "repo:same/name"},
        {"b": "model:muse"}], docs, {k: k for k in docs})
    assert keys["b"] == "unit:b"
    assert keys["d"] == "repo:owner/a" and keys["e"] == "repo:other/a"
    assert len(groups) == 5


def test_unsupported_named_object_stays_separate_but_direct_reply_can_attach():
    from ai_digest.phase2_subjects import subject_components
    docs = {
        "a": {"observations": [{"payload": {"post_id": "1", "text": "Astra release"}}]},
        "b": {"observations": [{"payload": {"post_id": "2", "text": "Useful", "references": [{"id": "1"}]}}]},
        "c": {"observations": [{"payload": {"text": "unrelated maths claim"}}]},
        "d": {"observations": [{"payload": {"text": "yes", "references": [{"id": "2"}]}}]},
    }
    groups, keys = subject_components(list(docs), [{pid: "object:gpt-6 astra" for pid in docs}], docs, {pid: pid for pid in docs})
    assert keys["a"] == keys["b"] == "object:gpt-6 astra"
    assert keys["c"] == "unit:c" and keys["d"] == "unit:d"
    assert sorted(map(len, groups)) == [1, 1, 2]
    groups, keys = subject_components(["c", "d"], [{"c": "object:invented", "d": "object:invented"}],
                                     docs, {pid: pid for pid in docs}, [["c", "d"]])
    assert groups == [["c", "d"]] and keys["c"] == keys["d"] == "unit:c"


def test_literal_paper_ids_and_unique_original_titles_resolve_without_name_guessing():
    from ai_digest.phase2_subjects import subject_components
    docs = {
        "a": {"observations": [{"item_type": "paper", "payload": {"arxiv_id": "2609.12345v2", "title": "My Study"}}]},
        "b": {"observations": [{"payload": {"text": "My Study results"}}]},
        "c": {"observations": [{"item_type": "paper", "payload": {"arxiv_id": "2609.54321", "title": "Other Study"}}]},
    }
    groups, keys = subject_components(list(docs), [{pid: "paper:my study" for pid in docs}], docs, {pid: pid for pid in docs})
    assert keys["a"] == keys["b"] == "paper:2609.12345"
    assert keys["c"] == "paper:2609.54321" and sorted(map(len, groups)) == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("fraction", [0, 0.2, 1])
async def test_real_admission_integration_replays_and_preserves_phase2(tmp_path, monkeypatch, fraction):
    items = {str(i): SourceItem(item_id=str(i), item_type="test", surface="test", source=["source_a", "source_b", "source_c"][i % 3],
             entity_key=f"entity:{i}", payload={"text": f"A concrete observation {i}"}) for i in range(20)}
    documents = build_phase2_unit_documents(build_observation_units(items), items)
    packages = [ResearchPackage(package_id=f"p{i}", label_zh=f"object {i}", scope_note_zh="test",
                 unit_ids=[d.unit_id]) for i, d in enumerate(documents)]
    root = tmp_path / "02_routing"
    atomic_write_jsonl(root / "units.jsonl", [d.model_dump(mode="json") for d in documents])
    atomic_write_jsonl(root / "labels.jsonl", [{"unit_id": d.unit_id, "signal": "unclear", "kind": "other",
                                               "local_group_id": str(i)} for i, d in enumerate(documents)])
    atomic_write_json(root / "phase2_manifest.json", {"contract": "semantic_labels_v1"})
    atomic_write_json(root / "packages.json", [p.model_dump() for p in packages])
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    calls = []
    async def rank(root, rows, interests, limit, runtime, runner):
        calls.append(limit)
        return [r["object_id"] for r in rows[:limit]], {"thread_id": "fake", "success": True}
    monkeypatch.setattr("ai_digest.phase3_admission.select_bounded", rank)
    runtime = RuntimeConfig(codex=CodexConfig(phase3_daily_agent_limit=15, phase3_exploration_fraction=fraction))
    first = await select_phase3_admission(tmp_path, packages, runtime, None)
    assert len(first.selected_object_ids) == 15
    assert len(first.exploration_object_ids) == int(15 * fraction)
    assert first.selected_object_ids[:15-int(15*fraction)] == [p.package_id for p in packages[:15-int(15*fraction)]]
    assert await select_phase3_admission(tmp_path, packages, runtime, None) == first
    assert calls == [15]
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    runtime.codex.phase3_daily_agent_limit = 0
    assert (await select_phase3_admission(tmp_path, packages, runtime, None)).selection_mode == "disabled"
    runtime.codex.phase3_daily_agent_limit = 20
    assert (await select_phase3_admission(tmp_path, packages, runtime, None)).selection_mode == "all"


@pytest.mark.asyncio
async def test_full_labels_keep_raw_tombstone_but_remove_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_digest.semantic_index.nearest_groups", lambda *args: {})
    class Runner:
        async def run(self, **kwargs):
            part = json.loads((kwargs["workspace"] / "input.json").read_text())
            atomic_write_json(kwargs["output_file"], {
                "labels": [{"unit_id": row["unit_id"], "signal": "unclear", "kind": "other", "local_group_id": row["unit_id"]} for row in part],
                "groups": [{"group_id": row["unit_id"], "title": "raw object"} for row in part]})
            return CodexResult(exit_code=0, thread_id="test")
    items = {
        "empty": SourceItem(item_id="empty", item_type="hn_story", source="test", surface="test",
                            entity_key="empty", content_status="tombstone", payload={}),
        "weak": SourceItem(item_id="weak", item_type="hn_story", source="test", surface="test",
                           entity_key="weak", content_status="preview", payload={"title": "Uncertain but concrete clue"})}
    await SemanticPhase2(RuntimeConfig(), Runner()).run(tmp_path, items, build_observation_units(items), "")
    labels, packages = validate_artifacts(tmp_path / "02_routing")
    assert len(labels) == 2 and len(packages) == 1
    assert sorted(label.research_eligibility for label in labels) == ["eligible", "no_readable_content"]
    assert len((tmp_path / "02_routing/units.jsonl").read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_named_subject_mode_materializes_and_replays(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_digest.semantic_index.nearest_groups",
                        lambda packages, *args: {p.package_id: [q.package_id for q in packages if q != p] for p in packages})
    class Runner:
        calls = 0
        async def run(self, **kwargs):
            self.calls += 1
            data = json.loads((kwargs["workspace"] / "input.json").read_text())
            if isinstance(data, list):
                output = {"labels": [{"unit_id": row["unit_id"], "signal": "present", "kind": "other", "local_group_id": row["unit_id"]} for row in data],
                          "groups": [{"group_id": row["unit_id"], "title": row["observations"][0]["payload"]["text"]} for row in data]}
            else:
                output = {g["group_id"]: "model:astra" if "Astra" in str(g["members"]) else "model:muse" for g in data["groups"]}
            atomic_write_json(kwargs["output_file"], output)
            return CodexResult(exit_code=0, thread_id="named-test")
    items = {str(i): SourceItem(item_id=str(i), item_type="test", source="test", surface="test", entity_key=str(i),
                               payload={"text": text}) for i, text in enumerate(["Astra demo", "Astra feedback", "Muse release"])}
    runner = Runner()
    engine = SemanticPhase2(RuntimeConfig(codex=CodexConfig(phase2_subject_keys=True)), runner)
    await engine.run(tmp_path, items, build_observation_units(items), "")
    _, packages = validate_artifacts(tmp_path / "02_routing")
    assert sorted(len(p.unit_ids) for p in packages) == [1, 2]
    assert next(p.label_zh for p in packages if len(p.unit_ids) == 2) == "astra"
    before = runner.calls
    await engine.run(tmp_path, items, build_observation_units(items), "")
    assert runner.calls == before
