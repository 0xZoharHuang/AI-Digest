from __future__ import annotations

import json

import pytest

from ai_digest.agent_phases import (
    ROUTING_SCHEMA,
    AgentPhases,
    _brief_agents_md,
    _brief_prompt,
    _bundle_context_manifest,
    _materialize_bundle_items,
    _read_and_validate_calibration,
    _read_and_validate_consolidation,
    _research_agents_md,
    _research_prompt,
    _stratified_batches,
)
from ai_digest.config import RuntimeConfig
from ai_digest.models import Bundle, RoutingOutput, SourceItem


def test_routing_requires_complete_coverage(tmp_path):
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared")
    phases = AgentPhases(runtime)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"item_ids": ["a", "b"]}))
    output = tmp_path / "output.json"
    output.write_text(
        json.dumps(
            {
                "bundles": [{"bundle_id": "b1", "label": "topic", "item_ids": ["a"]}],
                "assignments": [{"id": "a", "d": "r", "t": ["b1"]}],
            }
        )
    )
    routing, errors = phases._read_and_validate_routing(output, index)
    assert routing is not None
    assert any("missing 1" in error for error in errors)


def test_router_schema_requires_nullable_quiet_reason():
    assert "quiet_reason" in ROUTING_SCHEMA["required"]


def test_phase3_and_phase4_prompts_require_simplified_chinese():
    bundle = Bundle(bundle_id="topic", label="Topic", item_ids=["a"])
    routing = RoutingOutput(bundles=[bundle], assignments=[])
    assert "简体中文" in _research_agents_md()
    assert "Simplified Chinese" in _research_prompt(bundle)
    assert "简体中文" in _brief_agents_md()
    assert "Simplified Chinese" in _brief_prompt(routing, {"topic": "topic/report.md"})


def test_routing_accepts_research_watch_and_noise(tmp_path):
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared")
    phases = AgentPhases(runtime)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"item_ids": ["a", "b", "c"]}))
    output = tmp_path / "output.json"
    output.write_text(
        json.dumps(
            {
                "bundles": [{"bundle_id": "b1", "label": "topic", "item_ids": ["a"]}],
                "assignments": [
                    {"id": "a", "d": "r", "t": ["b1"]},
                    {"id": "b", "d": "w", "t": []},
                    {"id": "c", "d": "n", "t": []},
                ],
            }
        )
    )
    _, errors = phases._read_and_validate_routing(output, index)
    assert errors == []


def test_routing_rejects_bundle_assignment_mismatch(tmp_path):
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "shared")
    phases = AgentPhases(runtime)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"item_ids": ["a", "b"]}))
    output = tmp_path / "output.json"
    output.write_text(
        json.dumps(
            {
                "bundles": [{"bundle_id": "b1", "label": "topic", "item_ids": ["b"]}],
                "assignments": [
                    {"id": "a", "d": "r", "t": ["b1"]},
                    {"id": "b", "d": "w", "t": []},
                ],
            }
        )
    )
    _, errors = phases._read_and_validate_routing(output, index)
    assert any("membership does not match" in error for error in errors)


def test_consolidation_requires_exact_local_bundle_coverage(tmp_path):
    output = tmp_path / "consolidation.json"
    output.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "bundle_id": "global",
                        "label": "Global topic",
                        "local_bundle_ids": ["local_a"],
                    }
                ],
                "quiet_reason": None,
            }
        )
    )
    assert _read_and_validate_consolidation(output, {"local_a"})[0]["bundle_id"] == "global"
    with pytest.raises(RuntimeError, match="coverage mismatch"):
        _read_and_validate_consolidation(output, {"local_a", "local_b"})


def test_bundle_materialization_resolves_runtime_blob_and_writes_audit_manifest(tmp_path):
    runtime_root = tmp_path / "runtime"
    digest = "a" * 64
    blob = runtime_root / "store" / "blobs" / "aa" / f"{digest}.txt"
    blob.parent.mkdir(parents=True)
    blob.write_text("full article body")
    item = SourceItem(
        item_id="article:test",
        item_type="article",
        source="articles",
        surface="test",
        payload={"full_text_ref": f"sha256:{digest}.txt"},
    )
    workspace = tmp_path / "workspace"
    rows = _materialize_bundle_items([item], runtime_root, workspace)
    assert rows[0]["resolved_files"] == [f"source_files/{digest}.txt"]
    assert (workspace / rows[0]["resolved_files"][0]).read_text() == "full article body"
    manifest = _bundle_context_manifest(
        Bundle(bundle_id="topic", label="Topic", item_ids=[item.item_id]), rows
    )
    assert manifest["item_count"] == 1
    assert manifest["resolved_file_count"] == 1
    assert manifest["source_counts"] == {"articles": 1}


def test_stratified_batches_mix_sources_without_loss():
    items = [
        SourceItem(item_id=f"x:{index}", item_type="post", source="x", surface="list")
        for index in range(7)
    ] + [
        SourceItem(item_id=f"g:{index}", item_type="repo", source="github", surface="search")
        for index in range(5)
    ]
    batches = _stratified_batches(items, 4)
    assert all(len(batch) <= 4 for batch in batches)
    assert {item.item_id for batch in batches for item in batch} == {
        item.item_id for item in items
    }
    assert all({item.source for item in batch} == {"github", "x"} for batch in batches)


def test_calibration_requires_global_topics_and_watch_for_new_topic(tmp_path):
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"item_ids": ["a", "b"]}))
    output = tmp_path / "calibration.json"
    output.write_text(
        json.dumps(
            {
                "assignments": [
                    {"id": "a", "d": "r", "t": ["global"]},
                    {"id": "b", "d": "w", "t": []},
                ],
                "new_topic_suggestions": [{"label": "New", "item_ids": ["b"]}],
            }
        )
    )
    calibrated = _read_and_validate_calibration(output, index, {"global"})
    assert calibrated is not None
    output.write_text(output.read_text().replace('"d": "w"', '"d": "n"'))
    assert _read_and_validate_calibration(output, index, {"global"}) is None
