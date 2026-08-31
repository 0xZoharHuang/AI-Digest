from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ai_digest.codex_runner import CodexResult
from ai_digest.config import RuntimeConfig
from ai_digest.models import (
    ObservationUnit,
    Phase2Annotation,
    ResearchPackage,
    SourceItem,
)
from ai_digest.v3 import (
    V3Phases,
    build_observation_units,
    read_annotation_output,
    read_annotation_subset,
    split_oversize_packages,
    unit_batches,
    validate_packages,
    validate_research_manifest,
)


def test_observation_units_mechanically_merge_cross_source_entities():
    now = datetime(2026, 8, 31, tzinfo=UTC)
    items = {
        "x_list:1": SourceItem(
            item_id="x_list:1",
            item_type="x_post",
            source="x_list",
            surface="public_lists",
            ready_at=now,
            payload={"post_id": "1", "conversation_id": "1", "text": "root"},
        ),
        "x_for_you:1": SourceItem(
            item_id="x_for_you:1",
            item_type="x_post",
            source="x_for_you",
            surface="for_you",
            ready_at=now,
            payload={"post_id": "1", "text": "root"},
        ),
        "arxiv:1": SourceItem(
            item_id="arxiv:1",
            item_type="paper",
            source="arxiv",
            surface="category_feed",
            ready_at=now,
            payload={"arxiv_id": "2608.1", "title": "Paper"},
        ),
        "hf:1": SourceItem(
            item_id="hf:1",
            item_type="paper",
            source="huggingface",
            surface="daily_papers",
            ready_at=now,
            payload={"arxiv_id": "2608.1", "title": "Paper"},
        ),
    }
    units = build_observation_units(items)
    assert len(units) == 2
    assert sorted(len(unit.item_ids) for unit in units) == [2, 2]
    assert {tuple(unit.sources) for unit in units} == {
        ("arxiv", "huggingface"),
        ("x_for_you", "x_list"),
    }


def test_unit_batches_are_bounded_without_loss():
    units = [
        ObservationUnit(
            unit_id=f"u_{index:020d}",
            entity_key=f"item:{index}",
            item_ids=[str(index)],
            sources=["x_list"],
            summary="x" * 3000,
            projection={"text": "x" * 3000},
        )
        for index in range(450)
    ]
    batches = unit_batches(units)
    assert all(len(batch) <= 200 for batch in batches)
    assert {unit.unit_id for batch in batches for unit in batch} == {
        unit.unit_id for unit in units
    }


def test_packages_cover_every_investigate_unit_once():
    annotations = [
        Phase2Annotation(
            unit_id="u_a",
            disposition="investigate",
            summary_zh="A",
            reason="A",
        ),
        Phase2Annotation(
            unit_id="u_b",
            disposition="supporting",
            summary_zh="B",
            reason="B",
        ),
    ]
    package = ResearchPackage(
        package_id="p",
        label="P",
        investigate_unit_ids=["u_a"],
        supporting_unit_ids=["u_b"],
    )
    validate_packages([package], annotations)
    with pytest.raises(RuntimeError, match="coverage mismatch"):
        validate_packages([], annotations)


def test_partial_annotation_output_is_recoverable_without_being_accepted(tmp_path):
    output = tmp_path / "annotations.json"
    output.write_text(
        json.dumps(
            {
                "annotations": [
                    {
                        "unit_id": "u_a",
                        "disposition": "investigate",
                        "summary_zh": "A",
                        "reason": "A",
                        "entities": [],
                        "relation_hints": [],
                        "duplicate_of": None,
                    }
                ],
                "working_map": "# map",
            }
        )
    )
    subset = read_annotation_subset(output, {"u_a", "u_b"})
    assert subset is not None
    assert [value.unit_id for value in subset[0]] == ["u_a"]
    assert read_annotation_output(output, {"u_a", "u_b"}) is None


def test_oversize_package_is_split_deterministically():
    units = {
        f"u_{index}": ObservationUnit(
            unit_id=f"u_{index}",
            entity_key=f"item:{index}",
            item_ids=[str(index)],
            sources=["github"],
            summary="item",
        )
        for index in range(91)
    }
    package = ResearchPackage(
        package_id="large",
        label="Large",
        investigate_unit_ids=list(units),
    )
    result = split_oversize_packages([package], units)
    assert [len(value.investigate_unit_ids) for value in result] == [90, 1]


def test_research_manifest_records_missing_without_triggering_repair(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label="Package",
        investigate_unit_ids=["u_a", "u_b"],
    )
    (tmp_path / "dossier.md").write_text("# Dossier\n", encoding="utf-8")
    (tmp_path / "research_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "package",
                "dossier": "dossier.md",
                "subreports": [],
                "primary_unit_ids": ["u_a"],
                "unresolved_unit_ids": [],
                "missing_unit_ids": [],
                "status": "success",
            }
        ),
        encoding="utf-8",
    )
    manifest = validate_research_manifest(tmp_path, package)
    assert manifest.status == "partial"
    assert manifest.missing_unit_ids == ["u_b"]


def test_research_manifest_allows_attached_supporting_evidence(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label="Package",
        investigate_unit_ids=["u_primary"],
        supporting_unit_ids=["u_support"],
    )
    (tmp_path / "subreports").mkdir()
    (tmp_path / "dossier.md").write_text("# Dossier\n", encoding="utf-8")
    (tmp_path / "subreports" / "detail.md").write_text("# Detail\n", encoding="utf-8")
    (tmp_path / "research_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "package",
                "dossier": "dossier.md",
                "subreports": [
                    {
                        "slug": "detail",
                        "path": "subreports/detail.md",
                        "unit_ids": ["u_primary", "u_support"],
                    }
                ],
                "primary_unit_ids": ["u_primary"],
                "unresolved_unit_ids": [],
                "missing_unit_ids": [],
                "status": "success",
            }
        ),
        encoding="utf-8",
    )

    manifest = validate_research_manifest(tmp_path, package)
    assert manifest.missing_unit_ids == []


@pytest.mark.asyncio
async def test_phase2_uses_one_resumed_thread_and_writes_v3_artifacts(tmp_path):
    run = tmp_path / "runs" / "2026-08-31" / "attempt-0001"
    phase1 = run / "01_phase1"
    phase1.mkdir(parents=True)
    now = datetime(2026, 8, 31, tzinfo=UTC)
    item = SourceItem(
        item_id="x_list:1",
        item_type="x_post",
        source="x_list",
        surface="public_lists",
        ready_at=now,
        payload={"post_id": "1", "text": "new agent release"},
    )
    (phase1 / "x_list.jsonl").write_text(item.model_dump_json() + "\n", encoding="utf-8")
    (phase1 / "PHASE1_COMPLETE").write_text("complete\n", encoding="utf-8")

    class FakeRunner:
        calls: list[str | None] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs.get("resume_thread_id"))
            output = kwargs["output_file"]
            if output.name == "annotation_output.json":
                unit_id = json.loads((output.parent / "units.jsonl").read_text())["unit_id"]
                output.write_text(
                    json.dumps(
                        {
                            "annotations": [
                                {
                                    "unit_id": unit_id,
                                    "disposition": "investigate",
                                    "summary_zh": "新Agent发布",
                                    "reason": "需要核查",
                                    "entities": ["agent"],
                                    "relation_hints": [],
                                    "duplicate_of": None,
                                }
                            ],
                            "working_map": "# Map\n\n- Agent release",
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                annotation = json.loads(
                    (output.parent / "annotations.jsonl").read_text().splitlines()[0]
                )
                output.write_text(
                    json.dumps(
                        {
                            "packages": [
                                {
                                    "package_id": "agent_release",
                                    "label": "Agent release",
                                    "investigate_unit_ids": [annotation["unit_id"]],
                                    "supporting_unit_ids": [],
                                }
                            ],
                            "unassigned_supporting_unit_ids": [],
                        }
                    ),
                    encoding="utf-8",
                )
            return CodexResult(exit_code=0, thread_id="thread-one")

    runner = FakeRunner()
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue")
    routing = await V3Phases(runtime, runner).route(run)  # type: ignore[arg-type]
    assert runner.calls == [None, "thread-one"]
    assert [bundle.bundle_id for bundle in routing.bundles] == ["agent_release"]
    assert (run / "02_routing" / "units.jsonl").exists()
    assert (run / "02_routing" / "annotations.jsonl").exists()
    assert (run / "02_routing" / "packages.json").exists()
