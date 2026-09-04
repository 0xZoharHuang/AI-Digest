from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ai_digest.phase2_attention as attention_module
from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.config import RuntimeConfig
from ai_digest.models import (
    ObservationUnit,
    Phase2ResearchObject,
    Phase2RoutingDecision,
    Phase2UnitDocument,
    SourceItem,
)
from ai_digest.phase2_attention import (
    attention_source_lane,
    build_phase2_unit_documents,
    phase2_attention_agents_md,
    stratified_unit_documents,
    validate_attention_artifacts,
    validate_attention_selection,
    validate_editor_outputs,
)
from ai_digest.store import load_jsonl
from ai_digest.v3 import V3Phases


def _item(item_id: str, source: str, text: str) -> SourceItem:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    return SourceItem(
        item_id=item_id,
        item_type="test",
        source=source,
        surface="test",
        occurred_at=now,
        first_observed_at=now,
        handoff_at=now,
        ready_at=now,
        entity_key=f"entity:{item_id}",
        payload={"title": text[:80], "text": text},
    )


def _document(unit_id: str, source: str) -> Phase2UnitDocument:
    item = _item(f"{source}:{unit_id}", source, f"full text {unit_id}")
    return Phase2UnitDocument(
        unit_id=unit_id,
        entity_key=f"entity:{unit_id}",
        item_ids=[item.item_id],
        sources=[source],
        occurred_at=item.occurred_at,
        observations=[item],
    )


def test_phase2_unit_documents_preserve_normalized_source_without_truncation():
    text = "begin-" + "x" * 6000 + "-end"
    item = _item("x_list:1", "x_list", text)
    unit = ObservationUnit(
        unit_id="u_00000000000000000001",
        entity_key="x:1",
        item_ids=[item.item_id],
        sources=[item.source],
        occurred_at=item.occurred_at,
        summary="legacy preview",
        projection={"observations": [{"text": text[:1200]}]},
    )

    document = build_phase2_unit_documents([unit], {item.item_id: item})[0]

    assert document.observations[0].payload["text"] == text
    assert "-end" in document.model_dump_json()


def test_phase2_documents_are_interleaved_across_sources():
    documents = [
        _document("u_00000000000000000001", "arxiv"),
        _document("u_00000000000000000002", "arxiv"),
        _document("u_00000000000000000003", "github"),
        _document("u_00000000000000000004", "github"),
        _document("u_00000000000000000005", "x_list"),
        _document("u_00000000000000000006", "x_list"),
    ]

    ordered = stratified_unit_documents(documents)

    assert [value.sources[0] for value in ordered] == [
        "arxiv",
        "github",
        "x_list",
        "arxiv",
        "github",
        "x_list",
    ]


def test_phase2_source_lanes_are_broad_and_mechanical():
    assert attention_source_lane(_document("u_00000000000000000001", "arxiv")) == "papers"
    assert attention_source_lane(_document("u_00000000000000000002", "huggingface")) == "papers"
    assert attention_source_lane(_document("u_00000000000000000003", "github")) == "github"
    assert attention_source_lane(_document("u_00000000000000000004", "x_list")) == "social_media"


def test_attention_prompt_makes_archive_a_positive_source_aware_judgment():
    prompt = phase2_attention_agents_md()
    assert "首要损失是假阴性" in prompt
    assert "不能是未入候选集时的默认" in prompt
    assert "不确定但直接相关时选择 Watch" in prompt
    assert "全部 observations" in prompt
    assert "GitHub" in prompt and "Hacker News" in prompt and "X：" in prompt
    assert "脚本可用于枚举、搜索、连接、机械提取字段和验证覆盖" in prompt
    assert "Phase 2 不写 scope、研究问题" in prompt
    assert "三个一级子 Agent" in prompt
    assert "不得根据 regex" in prompt


def test_attention_selection_has_no_object_count_or_size_policy():
    decisions: dict[str, Phase2RoutingDecision] = {}
    objects = []
    for index in range(20):
        unit_id = f"u_{index:020x}"
        object_id = f"subject-{index}"
        decisions[unit_id] = Phase2RoutingDecision(
            unit_id=unit_id,
            route="research",
            object_id=object_id,
            reason_zh="值得继续查看",
        )
        objects.append(
            Phase2ResearchObject(
                object_id=object_id,
                label_zh=f"对象 {index}",
                unit_ids=[unit_id],
            )
        )
    watch_id = "u_ffffffffffffffffffff"
    decisions[watch_id] = Phase2RoutingDecision(
        unit_id=watch_id,
        route="watch",
        reason_zh="证据仍不足",
    )

    validate_attention_selection(decisions, objects)


def _write_final_outputs(root: Path, *, partial: bool = False) -> None:
    documents = [
        Phase2UnitDocument.model_validate(row)
        for row in load_jsonl(root / "units.jsonl")
    ]
    decision_rows = []
    for index, document in enumerate(documents):
        if partial and index:
            break
        text = document.observations[0].payload["text"]
        route = (
            "research"
            if "research" in text
            else "watch"
            if "watch" in text
            else "archive"
        )
        decision_rows.append(
            {
                "unit_id": document.unit_id,
                "route": route,
                "object_id": "candidate" if route == "research" else "",
                "reason_zh": "存在具体信号" if route != "archive" else "",
            }
        )
    with (root / "decisions.jsonl").open("w", encoding="utf-8") as handle:
        for row in decision_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    research = [row["unit_id"] for row in decision_rows if row["route"] == "research"]
    (root / "objects.json").write_text(
        json.dumps(
            [
                {
                    "object_id": "candidate",
                    "label_zh": "独立候选",
                    "unit_ids": research,
                }
            ]
            if research
            else [],
            ensure_ascii=False,
        )
    )


class LongEditorRunner:
    def __init__(self, *, fail_once: bool = False):
        self.calls: list[tuple[str, str | None, bool, str]] = []
        self.fail_once = fail_once

    async def run(self, **kwargs):  # type: ignore[no-untyped-def]
        workspace: Path = kwargs["workspace"]
        self.calls.append(
            (
                workspace.name,
                kwargs.get("resume_thread_id"),
                kwargs.get("agents", False),
                kwargs["sandbox"],
            )
        )
        _write_final_outputs(workspace, partial=self.fail_once)
        if self.fail_once:
            self.fail_once = False
            return CodexResult(
                exit_code=1,
                thread_id="attention-thread",
                error_class="authentication",
                error="temporary auth failure",
            )
        kwargs["output_file"].write_text(
            json.dumps({"status": "complete", "note": "done"})
        )
        return CodexResult(exit_code=0, thread_id="attention-thread")


def _sealed_run(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "2026-09-02" / "attempt-0001"
    phase1 = run / "01_phase1"
    phase1.mkdir(parents=True)
    items = [
        _item("arxiv:1", "arxiv", "research full original " + "a" * 3000),
        _item("github:2", "github", "archive ordinary repository " + "b" * 3000),
        _item("x_list:3", "x_list", "watch early claim " + "c" * 3000),
    ]
    for item in items:
        with (phase1 / f"{item.source}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(item.model_dump_json() + "\n")
    (phase1 / "PHASE1_COMPLETE").write_text("complete\n")
    (run / "interests.md").write_text("AI systems and robotics\n")
    return run


@pytest.mark.asyncio
async def test_attention_editor_uses_one_long_task_and_resumes_same_thread(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(attention_module, "PHASE2_ATTENTION_BATCH_MAX_UNITS", 1)
    monkeypatch.setattr(attention_module, "PHASE2_ATTENTION_BATCH_MAX_BYTES", 1_000_000)
    run = _sealed_run(tmp_path)
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue")
    first_runner = LongEditorRunner(fail_once=True)

    with pytest.raises(RetryableCodexError):
        await V3Phases(runtime, first_runner).route(  # type: ignore[arg-type]
            run, run / "interests.md"
        )
    assert first_runner.calls == [
        ("attention-editor-v2", None, True, "workspace-write")
    ]

    second_runner = LongEditorRunner()
    routing = await V3Phases(runtime, second_runner).route(  # type: ignore[arg-type]
        run, run / "interests.md"
    )
    assert second_runner.calls == [
        ("attention-editor-v2", "attention-thread", True, "workspace-write")
    ]
    root = run / "02_routing"
    validate_attention_artifacts(root)
    manifest = json.loads((root / "phase2_manifest.json").read_text())
    assert manifest["execution_mode"] == "single_long_editor_task"
    assert manifest["route_counts"] == {"archive": 1, "research": 1, "watch": 1}
    assert manifest["batch_count"] == 3
    assert manifest["object_count"] == 1
    assert len(load_jsonl(root / "decisions.jsonl")) == 3
    assert json.loads((root / "objects.json").read_text())[0]["object_id"] == "candidate"
    assert "a" * 3000 in (root / "units.jsonl").read_text()
    assert (root / "attention-editor-v2" / "lanes" / "papers").is_dir()
    assert (root / "attention-editor-v2" / "lanes" / "github").is_dir()
    assert (root / "attention-editor-v2" / "lanes" / "social_media").is_dir()
    assert {assignment.d for assignment in routing.assignments} == {"r", "w", "n"}

    cached_runner = LongEditorRunner()
    cached = await V3Phases(runtime, cached_runner).route(  # type: ignore[arg-type]
        run, run / "interests.md"
    )
    assert cached_runner.calls == []
    assert cached == routing


def test_editor_output_validator_rejects_incomplete_decisions(tmp_path):
    documents = [_document("u_00000000000000000001", "arxiv")]
    root = tmp_path
    with (root / "units.jsonl").open("w") as handle:
        handle.write(documents[0].model_dump_json() + "\n")
    _write_final_outputs(root, partial=True)
    validate_editor_outputs(root, documents)

    (root / "decisions.jsonl").write_text("")
    with pytest.raises(RuntimeError, match="coverage mismatch"):
        validate_editor_outputs(root, documents)
