import asyncio
import json
import shutil

import pytest

from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.config import RuntimeConfig
from ai_digest.models import Phase3Admission, ResearchArtifactManifest, ResearchPackage, SourceItem
from ai_digest.phase3_batches import batch_admission, pack_batches, prepare_batch, run_batch
from ai_digest.utils import atomic_write_json, atomic_write_jsonl, atomic_write_text


def test_batch_packing_is_bounded_and_replayable():
    rows = [{"object_id": str(i), "sources": [str(i % 3)], "unit_count": 1,
             "readable": True, "original_bytes": 100} for i in range(100)]
    batches = pack_batches(rows, {"1"}, 3, 20, 1500, "day")
    assert list(map(len, batches)) == [15, 15, 15]
    assert len({pid for b in batches for pid in b}) == 45
    assert "1" not in {pid for b in batches for pid in b}
    assert batches == pack_batches(list(reversed(rows)), {"1"}, 3, 20, 1500, "day")
    assert pack_batches(rows, set(), 3, 20, 99, "day") == []
    assert pack_batches(rows, set(), 0, 20, 1500, "day") == []


def test_admission_counts_jobs_not_packages_and_checks_membership():
    args = dict(schema_version=2, daily_agent_limit=3, concurrency=3, selection_mode="batch_sampling",
                available_object_ids=list("abcdef"), selected_object_ids=list("abcdef"),
                not_scheduled_object_ids=[], exploration_object_ids=list("bcdef"),
                tail_batches=[list("bc"), list("def")], tail_batch_size=3)
    assert len(Phase3Admission(**args).selected_object_ids) == 6
    for updates in [{"schema_version": 1}, {"tail_batches": [list("bcd"), list("def")]},
                    {"tail_batch_size": 2}, {"daily_agent_limit": 2}]:
        with pytest.raises(ValueError):
            Phase3Admission(**{**args, **updates})


def prepare(work, packages, *args):
    atomic_write_text(work / "AGENTS.md", "independent batch")
    atomic_write_json(work / "batch_manifest.json", {"packages": [p.model_dump() for p in packages]})
    for p in packages:
        atomic_write_json(work / "packages" / p.package_id / "sources/u.json", {"unit_ids": p.unit_ids})


def complete(work, package):
    root = work / "packages" / package.package_id
    atomic_write_json(root / "research_manifest.json", {"package_id": package.package_id,
        "main_report": None, "subreports": [], "reviewed_unit_ids": package.unit_ids, "status": "not_published"})
    atomic_write_jsonl(root / "intake.jsonl", [{"unit_id": uid, "research_use": "not_used", "note_zh": "已核查，无新增事实。"} for uid in package.unit_ids])
    atomic_write_jsonl(root / "evidence.jsonl", [{"claim": "未提供可核实的新主张", "status": "unknown", "evidence": [],
        "scope": "原始材料", "conflict": "", "related_unit_ids": package.unit_ids}])
    atomic_write_text(root / "decision.md", "已核查，原始材料没有可核实的新主张，暂不形成正式报告。")


@pytest.mark.asyncio
async def test_one_thread_recovers_only_pending_packages(monkeypatch, tmp_path):
    monkeypatch.setattr("ai_digest.phase3_batches.prepare_batch", prepare)
    packages = [ResearchPackage(package_id=pid, label_zh=pid, scope_note_zh="独立", unit_ids=["u" + pid]) for pid in "ab"]
    seen = []
    class Runner:
        async def run(self, **kwargs):
            assert kwargs["agents"] is False
            seen.append(kwargs.get("resume_thread_id"))
            work = kwargs["workspace"]
            progress = json.loads((work / "progress.json").read_text())
            if len(seen) == 1:
                complete(work, packages[0])
                return CodexResult(exit_code=1, thread_id="same-thread", error_class="quota")
            assert progress["pending"] == ["b"]
            complete(work, packages[1])
            return CodexResult(exit_code=0, thread_id="same-thread")
    runner = Runner()
    args = (tmp_path / "work", packages, {}, {}, {}, tmp_path / "run", RuntimeConfig(), runner)
    first = await run_batch(*args)
    assert set(first["completed"]) == {"a"} and set(first["errors"]) == {"b"}
    original = (tmp_path / "run/03_research/a/decision.md").read_bytes()
    second = await run_batch(*args)
    assert set(second["completed"]) == {"a", "b"} and not second["errors"]
    assert seen == [None, "same-thread"]
    assert (tmp_path / "run/03_research/a/decision.md").read_bytes() == original
    await run_batch(*args)
    assert len(seen) == 2
    changed = RuntimeConfig()
    changed.codex.research_model = "different"
    with pytest.raises(ValueError, match="changed inputs"):
        await run_batch(*args[:6], changed, runner)


@pytest.mark.asyncio
async def test_completed_package_mutation_blocks_resume(monkeypatch, tmp_path):
    monkeypatch.setattr("ai_digest.phase3_batches.prepare_batch", prepare)
    packages = [ResearchPackage(package_id=pid, label_zh=pid, scope_note_zh="独立", unit_ids=["u" + pid]) for pid in "ab"]
    class Runner:
        calls = 0
        async def run(self, **kwargs):
            self.calls += 1
            work = kwargs["workspace"]
            if self.calls == 1:
                complete(work, packages[0])
                return CodexResult(exit_code=1, thread_id="same-thread", error_class="network")
            atomic_write_text(work / "packages/a/decision.md", "覆盖已经完成的结果")
            complete(work, packages[1])
            return CodexResult(exit_code=0, thread_id="same-thread")
    args = (tmp_path / "work", packages, {}, {}, {}, tmp_path / "run", RuntimeConfig(), Runner())
    await run_batch(*args)
    canonical = (tmp_path / "run/03_research/a/decision.md").read_bytes()
    with pytest.raises(ValueError, match="altered"):
        await run_batch(*args)
    assert (tmp_path / "run/03_research/a/decision.md").read_bytes() == canonical
    with pytest.raises(ValueError, match="mutation"):
        await run_batch(*args)


@pytest.mark.asyncio
async def test_batch_admission_is_path_independent_and_preserves_phase2(monkeypatch, tmp_path):
    packages = [ResearchPackage(package_id=f"p{i}", label_zh="独立项目", scope_note_zh="独立", unit_ids=[f"u{i}"]) for i in range(100)]
    docs = [{"unit_id": f"u{i}", "sources": [str(i % 3)], "observations": [{"payload": {"title": f"Project {i}"}}]} for i in range(100)]
    root = tmp_path / "original"
    atomic_write_json(root / "00_run_manifest.json", {"run_id": "same-day"})
    atomic_write_jsonl(root / "02_routing/units.jsonl", docs)
    original = (root / "02_routing/units.jsonl").read_bytes()
    calls = []
    async def priority(run, packages, runtime, runner):
        limit = runtime.codex.phase3_daily_agent_limit
        calls.append(limit)
        ids = [p.package_id for p in packages]
        return Phase3Admission(daily_agent_limit=limit, concurrency=3, selection_mode="codex_priority",
            available_object_ids=ids, selected_object_ids=ids[:limit], not_scheduled_object_ids=ids[limit:],
            selector_model="test", selector_reasoning="medium", thread_id="rank")
    monkeypatch.setattr("ai_digest.v3.select_single_phase3_admission", priority)
    runtime = RuntimeConfig()
    runtime.codex.phase3_tail_batch_size = 20
    first = await batch_admission(root, packages, runtime, None)
    assert first.selected_object_ids[:12] == [f"p{i}" for i in range(12)]
    assert len(first.selected_object_ids) == 72 and list(map(len, first.tail_batches)) == [20, 20, 20]
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    assert await batch_admission(moved, packages, runtime, None) == first
    assert original == (root / "02_routing/units.jsonl").read_bytes()
    assert calls == [12, 12]


def test_real_batch_workspace_shares_context_without_subagents(tmp_path):
    from ai_digest.models import Phase2CatalogEntry
    from ai_digest.phase2_attention import build_phase2_unit_documents
    from ai_digest.v3 import build_observation_units
    items = {str(i): SourceItem(item_id=str(i), item_type="test", source="test", surface="test",
                               entity_key=str(i), payload={"title": f"Project {i}"}) for i in range(2)}
    units = build_observation_units(items)
    documents = build_phase2_unit_documents(units, items)
    packages = [ResearchPackage(package_id=f"p{i}", label_zh=f"Project {i}", scope_note_zh="独立",
                                unit_ids=[doc.unit_id]) for i, doc in enumerate(documents)]
    catalog = {p.unit_ids[0]: Phase2CatalogEntry(unit_id=p.unit_ids[0], package_id=p.package_id,
                  summary_zh=p.label_zh) for p in packages}
    work = tmp_path / "batch"
    prepare_batch(work, packages, {u.unit_id: u for u in units}, catalog, items, tmp_path / "run", RuntimeConfig())
    assert (work / "shared/READER.md").is_file() and (work / "shared/global_catalog.jsonl").is_file()
    for p in packages:
        assert not (work / "packages" / p.package_id / "READER.md").exists()
        assert (work / "packages" / p.package_id / "manifest.json").is_file()
    assert "最多派发四个" not in (work / "AGENTS.md").read_text()


@pytest.mark.asyncio
async def test_batch_rejects_input_mutation(monkeypatch, tmp_path):
    monkeypatch.setattr("ai_digest.phase3_batches.prepare_batch", prepare)
    package = ResearchPackage(package_id="a", label_zh="a", scope_note_zh="独立", unit_ids=["u"])
    class Runner:
        async def run(self, **kwargs):
            complete(kwargs["workspace"], package)
            atomic_write_text(kwargs["workspace"] / "AGENTS.md", "changed")
            return CodexResult(exit_code=0, thread_id="one")
    args = (tmp_path / "work", [package], {}, {}, {}, tmp_path / "run", RuntimeConfig(), Runner())
    with pytest.raises(ValueError, match="altered original"):
        await run_batch(*args)
    assert not (tmp_path / "run/03_research/a").exists()
    with pytest.raises(ValueError, match="requires inspection"):
        await run_batch(*args)


@pytest.mark.asyncio
async def test_batch_startup_quota_and_unsafe_resume(monkeypatch, tmp_path):
    monkeypatch.setattr("ai_digest.phase3_batches.prepare_batch", prepare)
    package = ResearchPackage(package_id="a", label_zh="a", scope_note_zh="独立", unit_ids=["u"])
    class Runner:
        async def run(self, **kwargs):
            return CodexResult(exit_code=1, error_class="quota")
    args = (tmp_path / "work", [package], {}, {}, {}, tmp_path / "run", RuntimeConfig(), Runner())
    with pytest.raises(RetryableCodexError):
        await run_batch(*args)
    (tmp_path / "outside").mkdir()
    (tmp_path / "work/unsafe").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        await run_batch(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel,expected_peak", [(False, 3), (True, 6)])
async def test_priority_and_tail_pool_concurrency(monkeypatch, tmp_path, parallel, expected_peak):
    from ai_digest.v3 import V3Phases
    packages = [ResearchPackage(package_id=f"p{i}", label_zh="独立", scope_note_zh="独立", unit_ids=[f"u{i}"]) for i in range(9)]
    ids = [p.package_id for p in packages]
    admission = Phase3Admission(schema_version=2, daily_agent_limit=6, concurrency=expected_peak,
        selection_mode="batch_sampling", available_object_ids=ids, selected_object_ids=ids,
        not_scheduled_object_ids=[], exploration_object_ids=ids[3:],
        tail_batches=[ids[3:5], ids[5:7], ids[7:9]], tail_batch_size=2)
    async def select(*args):
        return admission
    monkeypatch.setattr("ai_digest.v3.select_phase3_admission", select)
    monkeypatch.setattr("ai_digest.v3.load_phase3_inputs", lambda *args: (packages, {}, {}))
    monkeypatch.setattr("ai_digest.v3.load_phase1_items", lambda *args: {})
    monkeypatch.setattr("ai_digest.v3.materialize_research_workspace", lambda *args: None)
    def manifest(package):
        return ResearchArtifactManifest(package_id=package.package_id, main_report=None,
            reviewed_unit_ids=package.unit_ids, status="not_published")
    monkeypatch.setattr("ai_digest.v3.validate_research_manifest", lambda folder, package: manifest(package))
    active = peak = 0
    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
    class Runner:
        async def run(self, **kwargs):
            await work()
            atomic_write_json(kwargs["workspace"] / "research_manifest.json", {})
            return CodexResult(exit_code=0, thread_id="priority")
    async def batch(workspace, members, *args):
        await work()
        return {"completed": {p.package_id: manifest(p).model_dump() for p in members}, "errors": {}, "calls": []}
    monkeypatch.setattr("ai_digest.phase3_batches.run_batch", batch)
    runtime = RuntimeConfig()
    runtime.codex.phase3_tail_parallel_pool = parallel
    phases = V3Phases(runtime, Runner())
    assert await phases.research(tmp_path) == {}
    assert peak == expected_peak
    assert len(json.loads((tmp_path / "03_research/not_published.json").read_text())) == 9


def test_tail_directory_and_counts_are_idempotent(tmp_path):
    from ai_digest.run_counts import run_counts
    from ai_digest.v3 import append_run_status
    atomic_write_json(tmp_path / "01_phase1/source_health.json", {})
    atomic_write_jsonl(tmp_path / "02_routing/units.jsonl", [{"unit_id": "u"}, {"unit_id": "v"}])
    atomic_write_json(tmp_path / "02_routing/packages.json", [
        {"package_id": "a", "unit_ids": ["u"]}, {"package_id": "b", "unit_ids": ["v"]}])
    atomic_write_json(tmp_path / "03_research/phase3_admission.json", {"selected_object_ids": ["a", "b"],
        "exploration_object_ids": ["b"], "tail_batches": [["b"]]})
    atomic_write_json(tmp_path / "03_research/tail_decisions.json", [
        {"package_id": "b", "status": "not_published", "label": "独立线索", "decision": "原文信息不足，不能确认事件。"}])
    path = tmp_path / "04_brief/daily_brief.md"
    atomic_write_text(path, "## 研究报告\n\n[阅读](report://a)\n")
    append_run_status(path, tmp_path, {"a": "a/main_report.md"})
    first = path.read_text()
    append_run_status(path, tmp_path, {"a": "a/main_report.md"})
    assert path.read_text() == first
    assert "1 批长尾研究" in first and first.count("## 长尾处理目录") == 1
    assert run_counts(tmp_path)["research_jobs"] == 2
