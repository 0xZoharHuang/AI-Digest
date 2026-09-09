import json

import pytest

from ai_digest.models import ResearchPackage, SourceItem
from ai_digest.reading_view import unresolved_external_context, view
from ai_digest.research_contract import compile_manifest
from ai_digest.utils import atomic_write_json, atomic_write_jsonl


def test_reading_view_preserves_unknown_content_and_explicit_quotes():
    doc = {"unit_id": "u", "observations": [{"content_hash": "hash", "raw_refs": ["blob"],
        "occurred_at": "today", "payload": {"text": "Worth reading", "metrics": {"likes": 2},
            "future_field": {"content": "must remain"}, "references": [{"text": "A release", "type": "quoted"}]}}]}
    before = json.dumps(doc)
    result = view(doc)
    o = result["observations"][0]
    assert "content_hash" not in o and "raw_refs" not in o
    assert o["captured_context"]["references"][0]["text"] == "A release"
    assert o["payload"]["future_field"] == {"content": "must remain"}
    assert o["occurred_at"] == "today" and json.dumps(doc) == before


def test_quote_link_cannot_be_closed_as_pure_chatter():
    assert unresolved_external_context({"observations": [{"payload": {
        "text": "wow", "references": [{"type": "quoted", "text": "See https://t.co/unknown"}]}}]})


def test_reading_view_retains_time_when_event_time_is_absent():
    doc = {"observations": [{"first_observed_at": "2026-09-08T00:00:00Z", "occurred_at": None, "payload": {}}]}
    assert view(doc)["observations"][0]["first_observed_at"] == "2026-09-08T00:00:00Z"
    assert "first_observed_at" not in view(doc, "quoted-context-v1")["observations"][0]


def test_manifest_compilation_requires_authored_exact_intake(tmp_path):
    p = ResearchPackage(package_id="p", label_zh="研究", scope_note_zh="独立", unit_ids=["u"])
    atomic_write_json(tmp_path / "manifest_request.json", {"status": "not_published", "subreports": []})
    atomic_write_jsonl(tmp_path / "intake.jsonl", [])
    with pytest.raises(ValueError, match="exact intake"):
        compile_manifest(tmp_path, p)
    assert not (tmp_path / "research_manifest.json").exists()
    atomic_write_jsonl(tmp_path / "intake.jsonl", [{"unit_id": "u", "research_use": "not_used", "note_zh": "父帖不可访问，资料不足"}])
    compile_manifest(tmp_path, p)
    assert json.loads((tmp_path / "research_manifest.json").read_text()) == {
        "package_id": "p", "main_report": None, "subreports": [], "reviewed_unit_ids": ["u"], "status": "not_published"}


@pytest.mark.asyncio
async def test_admission_replay_never_reranks_or_restarts(monkeypatch, tmp_path):
    from ai_digest.config import RuntimeConfig
    from ai_digest.models import Phase3Admission
    from ai_digest.v3 import select_phase3_admission
    package = ResearchPackage(package_id="p", label_zh="研究", scope_note_zh="独立", unit_ids=["u"])
    frozen = Phase3Admission(schema_version=3, daily_agent_limit=15, concurrency=6,
        selection_mode="batch_sampling", available_object_ids=["p"], selected_object_ids=["p"],
        not_scheduled_object_ids=[], execution_batches=[["p"]], task_max_packages=20)
    atomic_write_json(tmp_path / "03_research/phase3_admission.json", frozen.model_dump())
    runtime = RuntimeConfig()
    runtime.codex.phase3_daily_agent_limit = 100
    assert await select_phase3_admission(tmp_path, [package], runtime, None) == frozen
    with pytest.raises(ValueError, match="frozen"):
        await select_phase3_admission(tmp_path, [], runtime, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_incomplete_phase2_keeps_its_reading_profile(tmp_path, legacy):
    from ai_digest.config import RuntimeConfig
    from ai_digest.phase2_labels import SemanticPhase2
    from ai_digest.v3 import build_observation_units
    item = SourceItem(item_id="x", source="x_list", surface="test", item_type="x_post", entity_key="x:1",
        payload={"post_id": "1", "text": "Worth reading", "references": [{"type": "quoted", "text": "A concrete release"}]})
    items = {"x": item}
    work = tmp_path / "02_routing/semantic_labels_v1"
    if legacy:
        atomic_write_json(work / "old_checkpoint.json", {})
    runtime = RuntimeConfig()
    runtime.codex.phase2_reading_view = True
    seen = []
    for _ in range(2):
        reader = SemanticPhase2(runtime, None)
        async def stop(work, data, schema, prompt):
            seen.append("captured_context" in data[0]["observations"][0])
            raise RuntimeError("test interruption")
        reader.call = stop
        with pytest.raises(RuntimeError, match="test interruption"):
            await reader.run(tmp_path, items, build_observation_units(items), "")
        runtime.codex.phase2_reading_view = False
    assert seen == [not legacy, not legacy]


def test_thread_usage_is_cumulative_and_unknown_is_not_zero():
    from ai_digest.thread_metrics import aggregate_usage
    calls = [{"thread_id": "a", "interrupted": True, "thread_metrics": {
        "status": "observed", "total_usage": {"input_tokens": 100, "output_tokens": 10}}},
        {"thread_id": "a", "usage": {"input_tokens": 50, "output_tokens": 5}, "thread_metrics": {
            "status": "observed", "total_usage": {"input_tokens": 150, "output_tokens": 15}}}]
    usage, complete = aggregate_usage(calls)
    assert usage["input_tokens"] == 150 and not complete
    assert aggregate_usage([{"thread_id": None, "interrupted": True}]) == ({}, False)


def test_thread_metrics_accumulates_counter_resets(tmp_path):
    from ai_digest.thread_metrics import thread_metrics
    tid = "01a08191-e18f-74e3-b07b-dc8b72e30eb7"
    records = [{"type": "session_meta", "payload": {"id": tid, "cwd": str(tmp_path)}}]
    for value in [100, 200, 50, 80]:
        records.append({"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": value, "output_tokens": 10}}}})
    atomic_write_jsonl(tmp_path / f"2026/09/08/rollout-{tid}.jsonl", records)
    result = thread_metrics(tid, tmp_path, tmp_path)
    assert result["total_usage"] == {"input_tokens": 280, "output_tokens": 20}
    assert result["compactions_observed"] == 0
