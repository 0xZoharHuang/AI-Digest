import asyncio
import json
import shutil

import pytest

from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.config import RuntimeConfig
from ai_digest.models import ResearchPackage
from ai_digest.phase3_reading import (
    admission,
    balanced_batches,
    collect,
    prepare,
    publication_population,
    reading_views,
    research,
    run_task,
    selection_hint,
)
from ai_digest.reading_task import PAGE_CHARS, deliver
from ai_digest.utils import atomic_write_json, atomic_write_jsonl, atomic_write_text


def packages(n):
    return [ResearchPackage(package_id=f"p{i}", label_zh=f"资料{i}", scope_note_zh="原文", unit_ids=[f"u{i}"]) for i in range(n)]


def documents(n):
    return [{"unit_id": f"u{i}", "item_ids": [f"item{i}"], "sources": ["github"],
             "observations": [{"source": "github", "change": "updated", "payload": {
                 "text": f"原始消息{i}", "url": f"https://example.com/{i}", "metrics": {"stars": 7},
                 "unknown_field": "保留"}, "content_hash": "storage-only"}]} for i in range(n)]


def decide(work, pid, status="skip", report_id=None):
    atomic_write_json(work / "results" / f"{pid}.json", {"status": status, "note": "已读，测试消息不提供新增机制。",
        "sources": ["https://example.com/source"], "report_id": report_id})


def read_all(work):
    for page in json.loads((work / "reading_manifest.json").read_text())["pages"]:
        deliver(work, page)


def test_scale_allocation_keeps_2000_in_15_tasks():
    ids = [f"p{i}" for i in range(2000)]
    size = {pid: 5_000_000 if i == 0 else 1500 + i for i, pid in enumerate(ids)}
    batches = balanced_batches(ids, size, 15)
    assert len(batches) == 15
    assert set(p for b in batches for p in b) == set(ids)
    assert sum(map(len, batches)) == 2000
    assert max(map(len, batches)) <= 134
    assert any("p0" in b for b in batches)
    with pytest.raises(ValueError):
        balanced_batches(ids, size, 14)


def test_views_preserve_semantic_metadata():
    view = reading_views(documents(1))["u0"]
    assert view["observations"][0]["payload"]["metrics"] == {"stars": 7}
    assert view["observations"][0]["payload"]["unknown_field"] == "保留"
    assert "content_hash" not in view["observations"][0]


def test_admission_hint_is_original_content_with_captured_quote_not_storage_hash():
    docs = documents(1)
    docs[0]["observations"][0]["payload"].update(text="值得读", references=[{"type": "quoted", "text": "发布新的 VLA 架构"}])
    value = json.dumps(selection_hint(docs), ensure_ascii=False)
    assert "发布新的 VLA 架构" in value and "值得读" in value
    assert "storage-only" not in value and "content_hash" not in value
    docs[0]["observations"][0]["payload"]["references"] = None
    assert selection_hint(docs)["samples_not_full_evidence"][0]["text_excerpt"] == "值得读"


def test_draft_note_not_completion_and_identity_anchor_is_original(tmp_path, capsys):
    work = tmp_path / "tasks/task"
    docs = documents(1)
    docs[0]["observations"][0]["payload"].update(full_name="nekooy/PiKit", description="Pi and Termux in an Android APK")
    prepare(work, packages(1), reading_views(docs), RuntimeConfig())
    manifest = json.loads((work / "reading_manifest.json").read_text())
    assert manifest["packages"]["p0"]["source_anchors"][0]["description"] == "Pi and Termux in an Android APK"
    atomic_write_json(work / "session.json", {"thread_id": "original"})
    read_all(work)
    decide(work, "p0", "pending")
    assert collect(work)["pending"] == ["p0"]
    assert collect(work)["completed"] == {}
    decide(work, "p0", "brief")
    assert list(collect(work)["completed"]) == ["p0"]
    capsys.readouterr()


def test_pagination_requires_every_part_and_protects_accepted(tmp_path, capsys):
    work = tmp_path / "tasks/task1"
    views = {"u0": {"text": "x" * 60_000}, "u1": {"text": "later"}}
    prepare(work, packages(2), views, RuntimeConfig())
    atomic_write_json(work / "session.json", {"thread_id": "original"})
    manifest = json.loads((work / "reading_manifest.json").read_text())
    assert all(len(p.read_text()) <= PAGE_CHARS for p in (work / "pages").glob("*"))
    decide(work, "p0")
    deliver(work, manifest["packages"]["p0"]["pages"][0])
    assert not collect(work)["completed"]
    read_all(work)
    capsys.readouterr()
    assert list(collect(work)["completed"]) == ["p0"]
    # Agent-editable progress cannot invent completion of p1.
    atomic_write_json(work / "progress.json", {"completed": {"p1": {}}})
    assert list(collect(work)["completed"]) == ["p0"]
    decide(work, "p0", "brief")
    with pytest.raises(ValueError, match="protected"):
        collect(work)


def test_shared_report_is_validated_once_and_cannot_claim_extra_package(tmp_path, capsys):
    work = tmp_path / "tasks/task1"
    prepare(work, packages(3), reading_views(documents(3)), RuntimeConfig())
    atomic_write_json(work / "session.json", {"thread_id": "original"})
    read_all(work)
    capsys.readouterr()
    decide(work, "p0", "report", "mechanism")
    decide(work, "p1", "report", "mechanism")
    folder = work / "reports/mechanism"
    atomic_write_json(folder / "report.json", {"package_ids": ["p0", "p1"], "subreports": []})
    atomic_write_text(folder / "main_report.md", "# 技术机制\n\n[来源](https://example.com/source)\n")
    atomic_write_jsonl(folder / "evidence.jsonl", [{"claim": "来源描述机制", "status": "source_claim",
        "evidence": ["https://example.com/source"], "related_unit_ids": ["u0", "u1"]}])
    value = collect(work)
    assert len(value["reports"]) == 1 and len(value["completed"]) == 2
    assert value["pending"] == ["p2"]
    atomic_write_text(folder / "main_report.md", "changed")
    with pytest.raises(ValueError, match="protected"):
        collect(work)


@pytest.mark.asyncio
async def test_resume_same_thread_no_rework(tmp_path, capsys):
    seen = []
    class Runner:
        async def run(self, **kw):
            seen.append(kw["resume_thread_id"])
            assert kw["agents"] is False and kw["model"] == "gpt-5.6-sol"
            work = kw["workspace"]
            atomic_write_json(kw["thread_checkpoint_path"], {"thread_id": "original"})
            read_all(work)
            if len(seen) == 1:
                decide(work, "p0")
                return CodexResult(exit_code=1, thread_id="original", error_class="capacity")
            decide(work, "p1", "brief")
            return CodexResult(exit_code=0, thread_id="original")
    args = (tmp_path / "tasks/t", packages(2), reading_views(documents(2)), RuntimeConfig(), Runner())
    with pytest.raises(RetryableCodexError):
        await run_task(*args)
    before = (args[0] / "results/p0.json").read_bytes()
    assert len((await run_task(*args))["completed"]) == 2
    assert (args[0] / "results/p0.json").read_bytes() == before
    await run_task(*args)
    assert seen == [None, "original"]
    capsys.readouterr()


@pytest.mark.asyncio
async def test_rank_underfill_does_not_drop_long_packages(monkeypatch, tmp_path):
    runtime = RuntimeConfig()
    runtime.codex.phase3_reading_target = 1000
    async def rank(*args):
        return [], {}
    monkeypatch.setattr("ai_digest.phase3_reading.select_bounded", rank)
    docs = documents(2001)
    atomic_write_jsonl(tmp_path / "02_routing/units.jsonl", docs)
    result = await admission(tmp_path, packages(2001), runtime, None)
    assert len(result.selected_object_ids) == 1000 and len(result.batches) == 15
    assert max(map(len, result.batches)) <= 134


@pytest.mark.asyncio
async def test_complete_outputs_cannot_hide_thread_identity_violation(tmp_path, capsys):
    runtime = RuntimeConfig()
    runtime.codex.phase3_reading_target = 1
    atomic_write_jsonl(tmp_path / "02_routing/units.jsonl", documents(1))
    selected = await admission(tmp_path, packages(1), runtime, None)
    class WrongIdentity:
        async def run(self, **kw):
            atomic_write_json(kw["thread_checkpoint_path"], {"thread_id": "different"})
            read_all(kw["workspace"])
            decide(kw["workspace"], "p0")
            return CodexResult(exit_code=0, thread_id="actual")
    with pytest.raises(ValueError, match="identity"):
        await research(tmp_path, packages(1), selected, runtime, WrongIdentity())
    assert not (tmp_path / "03_research/PHASE3_COMPLETE").exists()
    capsys.readouterr()


@pytest.mark.asyncio
async def test_research_projection_import_and_publish(monkeypatch, tmp_path, capsys):
    from ai_digest.pipeline import _import_research
    from ai_digest.publisher import validate_publish_inputs
    from ai_digest.run_counts import run_counts
    run = tmp_path / "worker/2026-09-20/attempt-0001"
    atomic_write_jsonl(run / "02_routing/units.jsonl", documents(3))
    atomic_write_json(run / "02_routing/packages.json", [p.model_dump() for p in packages(3)])
    atomic_write_json(run / "02_routing/phase2_manifest.json", {"contract": "unit_packages_v1"})
    runtime = RuntimeConfig()
    runtime.codex.phase3_reading_target = 3
    selected = await admission(run, packages(3), runtime, None)
    # One task to exercise a shared report across two independently owned packages.
    selected.execution_batches = [["p0", "p1", "p2"]]
    atomic_write_json(run / "03_research/phase3_admission.json", selected.model_dump())
    class Runner:
        async def run(self, **kw):
            work = kw["workspace"]
            atomic_write_json(kw["thread_checkpoint_path"], {"thread_id": "same"})
            read_all(work)
            for pid in ("p0", "p1"):
                decide(work, pid, "report", "shared")
            decide(work, "p2", "brief")
            folder = work / "reports/shared"
            atomic_write_json(folder / "report.json", {"package_ids": ["p0", "p1"], "subreports": []})
            atomic_write_text(folder / "main_report.md", "# 一份联合报告\n\n[来源](https://example.com/source)")
            atomic_write_jsonl(folder / "evidence.jsonl", [{"claim": "机制", "status": "source_claim",
                "evidence": ["https://example.com/source"], "related_unit_ids": ["u0", "u1"]}])
            return CodexResult(exit_code=0, thread_id="same")
    from ai_digest.v3 import V3Phases
    monkeypatch.setattr("ai_digest.v3.load_phase3_inputs", lambda _: (packages(3), {}, {}))
    successes = await V3Phases(runtime, Runner()).research(run)
    assert json.loads((run / "03_research/timing.json").read_text())["execution_jobs"] == 1
    capsys.readouterr()
    assert len(successes) == 1
    assert run_counts(run)["reviewed_information"] == 3
    owner = tmp_path / "owner/2026-09-20/attempt-0001"
    shutil.copytree(run / "02_routing", owner / "02_routing")
    _import_research(run, owner)
    assert json.loads((owner / "03_research/successes.json").read_text()) == successes
    rid = next(iter(successes))
    atomic_write_json(owner / "00_run_manifest.json", {"run_id": "test"})
    atomic_write_json(owner / "01_phase1/source_health.json", {})
    atomic_write_text(owner / "04_brief/daily_brief.md", f"# 日报\n\n[报告](report://{rid})")
    atomic_write_text(owner / "04_brief/watch.jsonl", "")
    atomic_write_json(owner / "04_brief/quality.json", {"required_report_ids": [rid], "linked_report_ids": [rid], "missing_report_ids": [], "status": "success", "watch_count": 0,
        "research_object_count": 3, "scheduled_research_count": 3, "not_scheduled_research_count": 0})
    assert validate_publish_inputs(owner, "success")["report_count"] == 1
    expected = {p.package_id: set(p.unit_ids) for p in packages(3)}
    projection = publication_population(owner / "03_research", expected, selected)
    assert projection[rid] == {"u0", "u1"} and projection["p2"] == {"u2"}
    from test_publisher import FakeLark

    from ai_digest.config import LarkConfig
    from ai_digest.publisher import LarkPublisher
    publisher = LarkPublisher(LarkConfig(space_id="space", receiver_open_id="owner"))
    publisher.cli = FakeLark()
    published = publisher.publish(owner, "success")
    assert "updates" in published.nodes and published.dm_sent
    written = len(publisher.cli.writes)
    publisher.publish(owner, "success")
    assert len(publisher.cli.writes) == written
    assert len(publisher.cli.messages) == 1
    message = publisher.cli.messages[0][0]
    assert "1 条简讯" in message and "3 包完成原文阅读" in message
    assert "未成稿结论" not in message


@pytest.mark.asyncio
async def test_2000_mock_readings_cover_every_package_without_model_calls(tmp_path, capsys):
    runtime = RuntimeConfig()
    runtime.codex.phase3_reading_target = 2000
    runtime.codex.phase3_tail_parallel_pool = True
    atomic_write_jsonl(tmp_path / "02_routing/units.jsonl", documents(2000))
    selected = await admission(tmp_path, packages(2000), runtime, None)
    active = peak = calls = 0
    class Runner:
        async def run(self, **kw):
            nonlocal active, peak, calls
            active += 1
            calls += 1
            peak = max(active, peak)
            work = kw["workspace"]
            atomic_write_json(kw["thread_checkpoint_path"], {"thread_id": work.name})
            read_all(work)
            for pid in json.loads((work / "reading_manifest.json").read_text())["packages"]:
                decide(work, pid)
            await asyncio.sleep(0.01)
            active -= 1
            return CodexResult(exit_code=0, thread_id=work.name)
    assert await research(tmp_path, packages(2000), selected, runtime, Runner()) == {}
    capsys.readouterr()
    results = json.loads((tmp_path / "03_research/reading_results.json").read_text())
    assert len(results["packages"]) == 2000
    assert calls == 15 and peak <= 6
    before = (tmp_path / "03_research/not_published.json").read_bytes()
    await research(tmp_path, packages(2000), selected, runtime, Runner())
    assert calls == 15 and (tmp_path / "03_research/not_published.json").read_bytes() == before
