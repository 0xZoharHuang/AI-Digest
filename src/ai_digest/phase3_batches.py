"""File-backed, independent packages researched by one persistent batch thread."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .codex_runner import CodexRunner, RetryableCodexError
from .config import RuntimeConfig
from .models import Phase3Admission, ResearchArtifactManifest, ResearchPackage
from .phase2_attention import file_sha256
from .phase2_labels import has_captured_anchor
from .phase3_admission import explore
from .store import load_jsonl
from .utils import atomic_write_json, atomic_write_text

VERSION = "independent-tail-batch-v1"


def pack_batches(rows: list[dict[str, Any]], excluded: set[str], slots: int,
                 size: int, max_bytes: int, seed: str) -> list[list[str]]:
    if slots <= 0:
        return []
    ordered, _ = explore(rows, excluded, len(rows), seed)
    costs = {row["object_id"]: row["original_bytes"] for row in rows}
    batches: list[list[str]] = [[] for _ in range(slots)]
    sizes = [0] * slots
    for pid in ordered:
        choices = [i for i in range(slots) if len(batches[i]) < size and sizes[i] + costs[pid] <= max_bytes]
        if choices:
            index = min(choices, key=lambda i: (len(batches[i]), sizes[i], i))
            batches[index].append(pid)
            sizes[index] += costs[pid]
        if all(len(batch) >= size for batch in batches):
            break
    return [batch for batch in batches if batch]


async def batch_admission(run_dir: Path, packages: list[ResearchPackage], runtime: RuntimeConfig,
                          runner: CodexRunner) -> Phase3Admission:
    from .v3 import select_single_phase3_admission

    limit = runtime.codex.phase3_daily_agent_limit
    slots = min(3, int(limit * runtime.codex.phase3_exploration_fraction))
    if not slots or len(packages) <= limit:
        return await select_single_phase3_admission(run_dir, packages, runtime, runner)
    priority_runtime = runtime.model_copy(deep=True)
    priority_runtime.codex.phase3_daily_agent_limit = limit - slots
    priority_runtime.codex.phase3_exploration_fraction = 0
    priority = await select_single_phase3_admission(run_dir, packages, priority_runtime, runner)
    docs: dict[str, Any] = {str(row["unit_id"]): row for row in load_jsonl(run_dir / "02_routing/units.jsonl")}
    rows = []
    for package in packages:
        documents = [docs[uid] for uid in package.unit_ids]
        sources = Counter(source for doc in documents for source in doc["sources"])
        rows.append({"object_id": package.package_id, "unit_count": len(package.unit_ids),
            "readable": any(has_captured_anchor(doc) for doc in documents),
            "primary_source": min(sources, key=lambda key: (-sources[key], key)),
            "original_bytes": len(json.dumps(documents, ensure_ascii=False).encode())})
    run_manifest = run_dir / "00_run_manifest.json"
    run_identity = json.loads(run_manifest.read_text()) if run_manifest.exists() else {}
    seed = hashlib.sha256(json.dumps([VERSION, run_identity.get("run_id", run_identity.get("date", "validation")),
        file_sha256(run_dir / "02_routing/units.jsonl"),
        priority.selected_object_ids, runtime.codex.phase3_tail_batch_size,
        runtime.codex.phase3_tail_batch_max_bytes], sort_keys=True).encode()).hexdigest()
    batches = pack_batches(rows, set(priority.selected_object_ids), slots,
        runtime.codex.phase3_tail_batch_size, runtime.codex.phase3_tail_batch_max_bytes, seed)
    tail = [pid for batch in batches for pid in batch]
    selected = [*priority.selected_object_ids, *tail]
    row_map = {row["object_id"]: row for row in rows}
    return Phase3Admission(schema_version=2, daily_agent_limit=limit,
        concurrency=runtime.codex.top_level_concurrency + (3 if runtime.codex.phase3_tail_parallel_pool else 0),
        selection_mode="codex_priority" if priority.thread_id else "batch_sampling",
        selector_model=priority.selector_model, selector_reasoning=priority.selector_reasoning,
        thread_id=priority.thread_id, available_object_ids=[p.package_id for p in packages],
        selected_object_ids=selected, not_scheduled_object_ids=[p.package_id for p in packages if p.package_id not in selected],
        selection_contract=VERSION, exploration_seed=seed, exploration_object_ids=tail,
        exploration_strata=dict(Counter(row_map[pid]["primary_source"] for pid in tail)),
        tail_batches=batches, tail_batch_size=runtime.codex.phase3_tail_batch_size)


def batch_instructions() -> str:
    from .v3 import phase3_agents_md

    independent = phase3_agents_md()
    independent = independent.replace("你可以推翻标签、重新聚类并自主决定研究深度。",
        "你可以质疑标签并自主决定研究深度，但不得改变本批各原始包的成员。")
    return ("# Independent research task\n\n"
        "你同时收到 batch_manifest.json 中的全部独立包，材料已在 packages/<package_id>/。"
        "一个 thread 完成本批所有包，不是逐包等待程序给任务。你自主安排读取、搜索、研究顺序和深度，"
        "但必须实际覆盖每包全部信息，不能因前几个研究深入而跳过尾部。"
        "不强求包之间存在联系，不合并包，不用一篇统一趋势报告替代独立结果。"
        "这是独立问题集合，不代表全部是低优先级长尾。每包实际阅读后自行决定深度；"
        "初始工作量估计不是研究上限。先识别各包的问题和证据缺口，再安排深入顺序，不能漏掉后半批。"
        "历史研究只作上下文：先判断新证据改变了什么，避免重复铺陈背景；不因对象同名就认定没有增量。"
        "共享上下文在 shared/：先读 READER.md 与 RESEARCH_METHOD.md；global_catalog 和历史只按明确线索检索。"
        "shared/packet_context.json若存在，可按identity_key找到同对象的其他独立问题；关联仅供检索，不代表必须合并。"
        "shared/history_packets.json若存在，可定位既有报告和已见证据；同对象已有报告不等于本次没有新问题。"
        "batch_manifest.json 的 reference_files 若存在，路径以任务根目录为基准，提供当天标准化原始记录的只读检索入口。"
        "先查目录，再按明确的unit_id或关键词读取相关原文；不要全量重读，也不要把参考包算成自己已完成的包。"
        "共享读者文件中的一包一Lead表示研究独立性；批次执行方式以本合同为准。"
        "目录与临时标签只用于定位，不是事实证据；没有实际取得父帖或媒体内容，就不能补出具体人物、视频情节或上文。"
        "资料没随包提供不等于公开一手资料不存在；不能仅因标题没写AI或机器人就放弃使能技术线索。"
        "对数据、仿真、硬件、能源或基础设施，应在必要核查中判断是否有具体、实质的使能连接；不要泛化成任何领域都相关。"
        "只声称实际完成过的核查；引用应指向实际读到的页面，若仅取得镜像或搜索片段须说明。"
        "不发布说明只写可证的具体原因，不添加未经核实的背景来增强理由。"
        "文件是真相与恢复依据；读取 progress.json，已由程序验证完成的包不得重做或修改。"
        "每包产物必须写在自己的 packages/<package_id>/，使用原 package_id 和原 unit_ids。"
        "每包都写 decision.md，用1至3句简体中文说明研究结论或不研究的具体原因，不能仅写价值不高。"
        "不研究也必须完成 intake、evidence 和 not_published manifest。失败不等于不值得研究。"
        "正式报告先给30秒能读懂的新增信息、意义和关键依据/限制，再按问题自然展开必要深度。"
        "不规定统一篇幅，不堆背景、不重复，不为显得深入而无限延展。"
        "全部完成后最终回复只给完成数量；不要在对话里重抄报告。\n\n"
        "以下是每个独立包的研究/产物合同；其中共享文件从 shared/读取，PACKAGE.md、manifest.json、"
        "catalog 和 sources 从相应包目录读取。批次内禁止重新聚类或改变任何包的成员。\n\n" + independent)


def prepare_batch(work: Path, packages: list[ResearchPackage], units: Any, catalog: Any,
                  items: Any, run_dir: Path, runtime: RuntimeConfig) -> None:
    from .v3 import materialize_research_workspace, safe_child

    profile_path = work / "authoring_profile.json"
    if profile_path.exists():
        profile = json.loads(profile_path.read_text())
    else:
        profile = {"version": "lean-v2" if runtime.codex.phase3_lean_instructions and not (work / "identity.json").exists() else "legacy"}
        atomic_write_json(profile_path, profile)
    if profile["version"] not in {"legacy", "lean-v1", "lean-v2"}:
        raise ValueError("unknown frozen research authoring profile")
    shared = work / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    for package in packages:
        folder = safe_child(work / "packages", package.package_id)
        materialize_research_workspace(folder, package, units, catalog, items, run_dir, runtime.runtime_root)
        for name in ("READER.md", "RESEARCH_METHOD.md", "global_catalog.jsonl", "history_index.md", "bootstrap_index.jsonl",
                     "history_packets.json", "packet_context.json"):
            source = folder / name
            if source.exists():
                target = shared / name
                if target.exists() and file_sha256(target) != file_sha256(source):
                    raise ValueError("batch shared context differs between packages")
                if not target.exists():
                    shutil.copy2(source, target)
                source.unlink()
        source_history = folder / "history"
        if source_history.is_dir():
            target_history = shared / "history"
            target_history.mkdir(exist_ok=True)
            for source in source_history.glob("*.md"):
                target = target_history / source.name
                if target.exists() and file_sha256(target) != file_sha256(source):
                    raise ValueError("historical evidence differs across packages")
                if not target.exists():
                    shutil.copy2(source, target)
                source.unlink()
            source_history.rmdir()
        atomic_write_text(folder / "AGENTS.md", "遵守 ../../AGENTS.md 的独立包研究合同；共享文件在 ../../shared/。禁止派发 subagents。\n")
    atomic_write_json(work / "batch_manifest.json", {"version": VERSION,
        "reference_files": [os.path.relpath(run_dir / "02_routing" / name, work)
                            for name in ("units.jsonl", "packages.json")
                            if runtime.codex.phase3_dynamic_tasks and (run_dir / "02_routing" / name).is_file()],
        "packages": [{"package_id": p.package_id, "label": p.label_zh, "unit_ids": p.unit_ids,
                      "path": f"packages/{p.package_id}"} for p in packages]})
    from .research_contract import instructions
    atomic_write_text(work / "AGENTS.md", instructions(profile["version"]) if profile["version"].startswith("lean-") else batch_instructions())


def validate_batch_package(folder: Path, package: ResearchPackage) -> ResearchArtifactManifest:
    from .v3 import assert_reader_output_is_clean, validate_research_manifest

    manifest = validate_research_manifest(folder, package)
    decision = folder / "decision.md"
    if decision.is_symlink() or not decision.is_file() or not decision.read_text().strip():
        raise ValueError("missing independent package decision")
    assert_reader_output_is_clean(decision, set(package.unit_ids))
    if any(path.is_symlink() for path in folder.rglob("*")):
        raise ValueError("batch package contains unsafe symlink")
    return manifest


async def run_batch(work: Path, packages: list[ResearchPackage], units: Any, catalog: Any,
                    items: Any, run_dir: Path, runtime: RuntimeConfig,
                    runner: CodexRunner, prompt_override: str | None = None) -> dict[str, Any]:
    from .v3 import codex_summary, safe_child

    if work.is_symlink() or (work.exists() and any(path.is_symlink() for path in work.rglob("*"))):
        raise ValueError("batch workspace contains unsafe symlinks")
    if (work / "INPUT_VIOLATION.json").exists():
        raise ValueError("batch input mutation requires inspection before resume")
    prepare_batch(work, packages, units, catalog, items, run_dir, runtime)
    reference_files = [run_dir / "02_routing" / name for name in ("units.jsonl", "packages.json")
                       if runtime.codex.phase3_dynamic_tasks and (run_dir / "02_routing" / name).is_file()]
    if any(path.is_symlink() for path in reference_files):
        raise ValueError("unsafe research reference file")
    if prompt_override is not None:
        atomic_write_text(work / "AGENTS.md", prompt_override)
    source_files = [p for folder in (work / "packages").glob("*/sources") for p in folder.rglob("*") if p.is_file()]
    protected_sources = {str(p.relative_to(work)): file_sha256(p) for p in source_files}
    prior_identity = work / "identity.json"
    prior_inputs = json.loads(prior_identity.read_text()).get("inputs", {}) if prior_identity.exists() else None
    source_files = [p for p in source_files if prior_inputs is None or str(p.relative_to(work)) in prior_inputs]
    identity: dict[str, Any] = {"version": VERSION, "model": runtime.codex.research_model,
        "reasoning": runtime.codex.research_reasoning,
        "inputs": {str(path.relative_to(work)): file_sha256(path)
                   for path in [work / "AGENTS.md", work / "batch_manifest.json", *sorted((work / "shared").rglob("*")),
                                *sorted(source_files)] if path.is_file()}}
    profile = work / "authoring_profile.json"
    existing_identity = work / "identity.json"
    include_profile = (not existing_identity.exists()
        or profile.name in json.loads(existing_identity.read_text()).get("inputs", {}))
    if profile.exists() and json.loads(profile.read_text())["version"].startswith("lean-") and include_profile:
        identity["inputs"][profile.name] = file_sha256(profile)
    identity_path = work / "identity.json"
    identity["inputs"].update({os.path.relpath(path, work): file_sha256(path) for path in reference_files})
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("cannot resume a batch with changed inputs/model/instructions")
    atomic_write_json(identity_path, identity)
    completed: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def collect() -> None:
        for package in packages:
            if package.package_id in completed:
                continue
            source = safe_child(work / "packages", package.package_id)
            target = safe_child(run_dir / "03_research", package.package_id)
            try:
                # Canonical accepted results survive retries and cannot be overwritten by the batch.
                if target.exists():
                    try:
                        manifest = validate_batch_package(target, package)
                    except (ValueError, RuntimeError, OSError):
                        pass
                    else:
                        completed[package.package_id] = manifest.model_dump(mode="json")
                        continue
                profile = work / "authoring_profile.json"
                if profile.exists() and json.loads(profile.read_text())["version"].startswith("lean-"):
                    from .research_contract import compile_manifest
                    compile_manifest(source, package)
                manifest = validate_batch_package(source, package)
                if not (work / "session.json").exists():
                    raise ValueError("batch output has no thread checkpoint")
                staged = target.parent / f".{package.package_id}-batch-import"
                if staged.exists():
                    if staged.is_symlink() or not staged.is_dir():
                        raise ValueError("unsafe batch import path")
                    history = work / "previous-incomplete"
                    history.mkdir(exist_ok=True)
                    staged.rename(history / f"import-{package.package_id}-{time.time_ns()}")
                shutil.copytree(source, staged)
                if target.exists():
                    history = work / "previous-incomplete"
                    history.mkdir(exist_ok=True)
                    target.rename(history / f"{package.package_id}-{time.time_ns()}")
                staged.rename(target)
                origin = os.path.relpath(work, target)
                atomic_write_json(target / "batch_origin.json", {"batch_path": origin,
                    "identity_hash": file_sha256(work / "identity.json")})
                atomic_write_text(target / "AGENTS.md", f"这是已验证的独立包产物。原批次规则位于 {origin}/AGENTS.md，"
                    f"共享读者与方法文件位于 {origin}/shared/；恢复研究使用原批次 thread，不在此另开逐包 agent。\n")
                completed[package.package_id] = manifest.model_dump(mode="json")
                errors.pop(package.package_id, None)
            except (ValueError, RuntimeError, OSError) as error:
                errors[package.package_id] = str(error)
        atomic_write_json(work / "progress.json", {"completed": completed,
            "pending": [p.package_id for p in packages if p.package_id not in completed], "errors": errors})

    collect()
    accepted_files: dict[str, str] = {}
    for pid in completed:
        for folder in (work / "packages" / pid, safe_child(run_dir / "03_research", pid)):
            for path in folder.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    accepted_files[os.path.relpath(path, work)] = file_sha256(path)
    calls = []
    receipt = work / "receipt.json"
    if receipt.exists():
        calls = json.loads(receipt.read_text()).get("calls", [])
    previous_call_count = len(calls)
    known_threads = {c["thread_id"] for c in calls if c.get("thread_id")}
    checkpoint = work / "session.json"
    if checkpoint.exists():
        tid = json.loads(checkpoint.read_text()).get("thread_id")
        if tid:
            known_threads.add(tid)
    if len(known_threads) > 1:
        raise RuntimeError("research task has conflicting thread identities")
    if calls and not known_threads:
        raise RuntimeError("previous startup has no recoverable thread; refusing replacement thread")
    if known_threads and not checkpoint.exists():
        atomic_write_json(checkpoint, {"thread_id": next(iter(known_threads))})
    started = time.monotonic()
    for attempt in range(2):
        if len(completed) == len(packages):
            break
        checkpoint = work / "session.json"
        thread_id = json.loads(checkpoint.read_text()).get("thread_id") if checkpoint.exists() else None
        prompt = ("读取 AGENTS.md、batch_manifest.json 与 progress.json，完整处理全部未完成独立包。"
                  "所有材料已提供，自主安排研究，不另开 agent。已验证完成的包不要修改。")
        if attempt:
            prompt += " 修复 progress.json 记录的遗漏或产物错误，不降低研究质量，不重做已完成包。"
        call_started = time.monotonic()
        call_started_at = datetime.now(UTC).isoformat()
        from .thread_metrics import thread_metrics
        try:
            result = await runner.run(workspace=work, prompt=prompt,
                model=runtime.codex.research_model, reasoning=runtime.codex.research_reasoning,
                sandbox="workspace-write", web_search=True, agents=False,
                reference_files=reference_files,
                resume_thread_id=thread_id, thread_checkpoint_path=checkpoint)
        except BaseException:
            interrupted_id = json.loads(checkpoint.read_text()).get("thread_id") if checkpoint.exists() else thread_id
            calls.append({"thread_id": interrupted_id, "interrupted": True,
                "thread_metrics": thread_metrics(interrupted_id, work), "elapsed_seconds": time.monotonic() - call_started})
            atomic_write_json(receipt, {"calls": calls, "completed": list(completed)})
            changed = [name for name, expected in {**identity["inputs"], **protected_sources, **accepted_files}.items()
                       if (work / name).is_symlink() or not (work / name).is_file() or file_sha256(work / name) != expected]
            if changed:
                atomic_write_json(work / "INPUT_VIOLATION.json", {"changed_files": changed})
            raise
        if thread_id and result.thread_id and result.thread_id != thread_id:
            raise RuntimeError("batch changed its thread identity")
        if not result.thread_id and not thread_id:
            calls.append(codex_summary(result))
            atomic_write_json(receipt, {"calls": calls, "completed": list(completed)})
            if not result.success:
                raise RetryableCodexError("batch startup", result)
            raise RuntimeError("batch missing its thread identity")
        if result.thread_id and not checkpoint.exists():
            atomic_write_json(checkpoint, {"thread_id": result.thread_id})
        calls.append({**codex_summary(result), "elapsed_seconds": time.monotonic() - call_started,
            "thread_metrics": thread_metrics(result.thread_id or thread_id, work),
            "started_at": call_started_at, "completed_at": datetime.now(UTC).isoformat(),
            "web_search_events": sum(event.get("item", {}).get("type") == "web_search"
                                     and event.get("type") == "item.completed" for event in result.events)})
        atomic_write_json(receipt, {"calls": calls, "completed": list(completed)})
        changed = [name for name, digest in {**identity["inputs"], **protected_sources, **accepted_files}.items()
                   if (work / name).is_symlink() or not (work / name).is_file() or file_sha256(work / name) != digest]
        if changed:
            atomic_write_json(work / "INPUT_VIOLATION.json", {"changed_files": changed})
            raise ValueError("batch altered original inputs or shared instructions")
        collect()
        for pid in completed:
            for folder in (work / "packages" / pid, safe_child(run_dir / "03_research", pid)):
                for path in folder.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        accepted_files[os.path.relpath(path, work)] = file_sha256(path)
        if not result.success:
            break
    from .thread_metrics import thread_metrics
    refreshed = set()
    for call in reversed(calls):
        tid = call.get("thread_id")
        if tid and tid not in refreshed:
            observed = thread_metrics(tid, work)
            if observed.get("status") == "observed":
                call["thread_metrics"] = observed
            refreshed.add(tid)
    output = {"completed": completed, "errors": {pid: reason for pid, reason in errors.items() if pid not in completed},
              "calls": calls, "executed_calls_this_invocation": len(calls) - previous_call_count,
              "elapsed_seconds": time.monotonic() - started}
    atomic_write_json(receipt, output)
    return output
