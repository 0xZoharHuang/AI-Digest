from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_digest.config import LarkConfig
from ai_digest.models import PublishNode
from ai_digest.publisher import (
    LarkError,
    LarkPublisher,
    _extract_envelope,
    _rewrite_report_links,
    validate_publish_inputs,
)


class FakeLark:
    def __init__(self):
        self.nodes: dict[tuple[str | None, str], PublishNode] = {}
        self.writes: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def list_nodes(self, parent_token: str | None = None):  # type: ignore[no-untyped-def]
        return [
            {
                "title": title,
                "node_token": node.node_token,
                "obj_token": node.obj_token,
            }
            for (parent, title), node in self.nodes.items()
            if parent == parent_token
        ]

    def ensure_node(self, title: str, parent_token: str | None = None) -> PublishNode:
        key = (parent_token, title)
        if key not in self.nodes:
            token = f"node-{len(self.nodes)}"
            self.nodes[key] = PublishNode(
                key=token,
                title=title,
                node_token=token,
                obj_token=f"doc-{len(self.nodes)}",
                url=f"https://lark.test/{token}",
            )
        return self.nodes[key]

    def write_markdown(
        self,
        node: PublishNode,
        content: str,
        workdir: Path,
        *,
        required_substrings: list[str] | None = None,
    ) -> str:
        self.writes.append((node.node_token, content))
        return "1"

    def send_dm(self, markdown: str, idempotency_key: str) -> dict[str, str]:
        self.messages.append((markdown, idempotency_key))
        return {"message_id": "om-test", "chat_id": "oc-test"}

    def delete_node(self, node: PublishNode) -> None:
        self.deleted.append(node.node_token)


def test_lark_publisher_builds_tree_rewrites_links_and_is_idempotent(tmp_path):
    run_dir = tmp_path / "runs" / "2026-08-30" / "attempt-0001"
    report = run_dir / "03_research" / "b1" / "report.md"
    report.parent.mkdir(parents=True)
    (run_dir / "00_run_manifest.json").write_text(json.dumps({"run_id": "2026-08-30-a0001-test"}))
    report.write_text("# Report One\n\nBody")
    (run_dir / "03_research" / "successes.json").write_text(
        json.dumps({"b1": "b1/report.md"})
    )
    (run_dir / "03_research" / "failures.json").write_text("[]")
    health = run_dir / "01_phase1" / "source_health.json"
    health.parent.mkdir(parents=True)
    health.write_text("{}")
    brief = run_dir / "04_brief" / "daily_brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Brief\n\n[Full](report://b1)")
    (run_dir / "04_brief" / "watch.jsonl").write_text("")

    publisher = LarkPublisher(LarkConfig(space_id="space", receiver_open_id="user"))
    fake = FakeLark()
    publisher.cli = fake  # type: ignore[assignment]
    first = publisher.publish(run_dir, "SUCCESS")
    manifest_path = run_dir / "05_publish" / "publish_manifest.json"
    stored = json.loads(manifest_path.read_text())
    stored["nodes"]["report:stale"] = PublishNode(
        key="stale",
        title="Stale",
        node_token="node-stale",
        obj_token="doc-stale",
    ).model_dump(mode="json")
    manifest_path.write_text(json.dumps(stored))
    second = publisher.publish(run_dir, "SUCCESS")
    brief.write_text("# Brief updated\n\n[Full](report://b1)")
    third = publisher.publish(run_dir, "SUCCESS")

    assert first.status == "success"
    assert second.dm_sent
    assert second.dm_identity == "bot"
    assert second.dm_message_id == "om-test"
    assert second.dm_chat_id == "oc-test"
    assert third.artifact_hash != first.artifact_hash
    assert len(fake.messages) == 2
    assert fake.messages[0][1] != fake.messages[1][1]
    assert any("https://lark.test/" in content for _, content in fake.writes)
    assert third.navigation_version == 1
    assert any("日报索引" in content for _, content in fake.writes)
    assert fake.deleted == ["node-stale"]


def test_lark_envelope_parser_ignores_progress_lines():
    assert _extract_envelope('Creating node...\n{"ok":true,"data":{"x":1}}\n')["ok"] is True


def test_report_link_rewrite_handles_prefix_bundle_ids_exactly():
    content = "[A](report://agent) [B](report://agent-tools)"
    rewritten = _rewrite_report_links(
        content,
        {"agent": "https://lark.test/a", "agent-tools": "https://lark.test/b"},
    )
    assert rewritten == "[A](https://lark.test/a) [B](https://lark.test/b)"


def test_v3_publisher_nests_subreports_and_rewrites_all_links(tmp_path):
    run_dir = tmp_path / "runs" / "2026-08-31" / "attempt-0001"
    package = run_dir / "03_research" / "physical_ai"
    subreports = package / "subreports"
    subreports.mkdir(parents=True)
    (run_dir / "00_run_manifest.json").write_text(json.dumps({"run_id": "run-v3"}))
    (package / "dossier.md").write_text(
        "# Physical AI\n\n[Detail](subreport://physical_ai/runtime)"
    )
    (subreports / "runtime.md").write_text("# Runtime\n\nBody")
    (package / "research_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "physical_ai",
                "dossier": "dossier.md",
                "subreports": [
                    {
                        "slug": "runtime",
                        "path": "subreports/runtime.md",
                        "unit_ids": ["u_a"],
                    }
                ],
                "primary_unit_ids": ["u_a"],
                "unresolved_unit_ids": [],
                "missing_unit_ids": [],
                "status": "success",
            }
        )
    )
    (run_dir / "03_research" / "successes.json").write_text(
        json.dumps({"physical_ai": "physical_ai/dossier.md"})
    )
    (run_dir / "03_research" / "failures.json").write_text("[]")
    health = run_dir / "01_phase1" / "source_health.json"
    health.parent.mkdir(parents=True)
    health.write_text("{}")
    brief = run_dir / "04_brief" / "daily_brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text(
        "# Brief\n\n[Dossier](report://physical_ai) "
        "[Runtime](subreport://physical_ai/runtime)"
    )
    (run_dir / "04_brief" / "watch.jsonl").write_text("")

    publisher = LarkPublisher(LarkConfig(space_id="space", receiver_open_id="user"))
    fake = FakeLark()
    publisher.cli = fake  # type: ignore[assignment]
    result = publisher.publish(run_dir, "SUCCESS")

    assert result.status == "success"
    assert any(title == "Runtime" for _, title in fake.nodes)
    assert all("subreport://" not in content for _, content in fake.writes)
    assert all("report://" not in content for _, content in fake.writes)
    assert any("子报告导航" in content for _, content in fake.writes)
    assert any("> 导航：" in content for _, content in fake.writes)


def test_publish_preflight_parses_unicode_jsonl_and_prevents_partial_writes(tmp_path):
    run_dir = tmp_path / "runs" / "2026-08-31" / "attempt-0001"
    report = run_dir / "03_research" / "b1" / "report.md"
    report.parent.mkdir(parents=True)
    (run_dir / "00_run_manifest.json").write_text(json.dumps({"run_id": "run-preflight"}))
    report.write_text("# Report\n")
    (run_dir / "03_research" / "successes.json").write_text(
        json.dumps({"b1": "b1/report.md"})
    )
    (run_dir / "03_research" / "failures.json").write_text("[]")
    health = run_dir / "01_phase1" / "source_health.json"
    health.parent.mkdir(parents=True)
    health.write_text("{}")
    brief = run_dir / "04_brief" / "daily_brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Brief\n\n[Report](report://b1)")
    (run_dir / "04_brief" / "watch.jsonl").write_text(
        json.dumps({"text": "A\u2028B\u2029C"}, ensure_ascii=False) + "\n"
    )

    result = validate_publish_inputs(run_dir, "SUCCESS")
    assert result["watch_count"] == 1

    brief.write_text("# Brief\n\n[Unknown](report://unknown)")
    publisher = LarkPublisher(LarkConfig(space_id="space", receiver_open_id="user"))
    fake = FakeLark()
    publisher.cli = fake  # type: ignore[assignment]
    with pytest.raises(LarkError, match="missing report links"):
        publisher.publish(run_dir, "SUCCESS")
    assert fake.nodes == {}
