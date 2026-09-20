"""Resume one existing research thread to fix a rejected ledger; never publishes."""
import argparse
import asyncio
import json
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_runtime_config
from ai_digest.models import ResearchPackage
from ai_digest.phase2_attention import file_sha256
from ai_digest.phase3_batches import validate_batch_package
from ai_digest.utils import atomic_write_json


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    folder = workspace / "packages" / args.package
    package = ResearchPackage.model_validate(json.loads((folder / "manifest.json").read_text())["package"])
    thread = json.loads((workspace / "session.json").read_text())["thread_id"]
    protected = {str(p.relative_to(workspace)): file_sha256(p) for p in workspace.rglob("*")
                 if p.is_file() and not p.is_symlink() and p != folder / "evidence.jsonl"}
    try:
        validate_batch_package(folder, package)
        error = None
    except ValueError as failure:
        error = str(failure)
    if error is not None:
        runtime = load_runtime_config()
        runner = CodexRunner(runtime.codex.binary)
        result = await runner.run(workspace=workspace, model="gpt-5.6-sol", reasoning="medium",
            sandbox="workspace-write", web_search=False, agents=False, resume_thread_id=thread,
            prompt=f"原任务归档后有一个产物未通过校验。当前工作目录已经移动到 {workspace}，请仅用相对路径。"
                   f"只修复 packages/{args.package}/evidence.jsonl。错误：{error}。"
                   "先读取该包 manifest.json、sources 和 evidence.jsonl，核对每项证据与原始 ID 的映射。"
                   "错误是 ID 转录缺字时，从 manifest 复制正确完整 ID；不得删证据、放宽校验或随意映射。"
                   "其他文件、报告、已完成包和 session.json 均不得修改，不新增任务，不联网重复研究。"
                   "完成后简短说明具体修复。")
        if not result.success or result.thread_id != thread:
            raise RuntimeError("existing research thread repair did not complete")
    changed = [name for name, expected in protected.items() if file_sha256(workspace / name) != expected]
    if changed:
        raise RuntimeError(f"repair changed protected files: {changed}")
    manifest = validate_batch_package(folder, package)
    receipt = {"status": "passed", "thread_id": thread, "package_id": package.package_id,
               "original_error": error, "protected_files_unchanged": len(protected),
               "evidence_hash": file_sha256(folder / "evidence.jsonl"),
               "manifest": manifest.model_dump(mode="json"), "live_publish_calls": 0}
    atomic_write_json(args.receipt, receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    asyncio.run(main())
