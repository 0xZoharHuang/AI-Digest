from __future__ import annotations

import json

from ai_digest.agent_phases import (
    ROUTING_SCHEMA,
    AgentPhases,
    _brief_agents_md,
    _brief_prompt,
    _research_agents_md,
    _research_prompt,
)
from ai_digest.config import RuntimeConfig
from ai_digest.models import Bundle, RoutingOutput


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
