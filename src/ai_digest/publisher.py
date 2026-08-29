from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from .config import LarkConfig, resolve_binary
from .models import PublishManifest, PublishNode
from .utils import atomic_write_json, atomic_write_text


class LarkError(RuntimeError):
    pass


class LarkCLI:
    def __init__(self, config: LarkConfig):
        self.config = config
        self.binary = resolve_binary(config.binary)

    def call(self, args: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
        process = subprocess.run(
            [self.binary, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        try:
            envelope = _extract_envelope(process.stdout or process.stderr)
        except json.JSONDecodeError as error:
            raise LarkError(
                f"lark-cli returned non-JSON (exit {process.returncode}): {process.stderr[-1000:]}"
            ) from error
        if process.returncode != 0 or envelope.get("ok") is not True:
            detail = envelope.get("error") or envelope
            raise LarkError(f"lark-cli failed: {detail}")
        return envelope.get("data") or {}

    def list_nodes(self, parent_token: str | None = None) -> list[dict[str, Any]]:
        args = [
            "wiki",
            "+node-list",
            "--space-id",
            self.config.space_id,
            "--page-all",
            "--as",
            self.config.identity,
        ]
        if parent_token:
            args.extend(["--parent-node-token", parent_token])
        data = self.call(args)
        for key in ("items", "nodes", "results"):
            if isinstance(data.get(key), list):
                return cast(list[dict[str, Any]], data[key])
        return []

    def ensure_node(self, title: str, parent_token: str | None = None) -> PublishNode:
        for row in self.list_nodes(parent_token):
            if row.get("title") == title:
                return self._node(title, row)
        args = ["wiki", "+node-create", "--title", title, "--obj-type", "docx"]
        if parent_token:
            args.extend(["--parent-node-token", parent_token])
        else:
            args.extend(["--space-id", self.config.space_id])
        args.extend(["--as", self.config.identity])
        data = self.call(args)
        return self._node(title, data)

    def write_markdown(self, node: PublishNode, content: str, workdir: Path) -> str:
        source = workdir / f"lark-{node.key}.md"
        atomic_write_text(source, content)
        data = self.call(
            [
                "docs",
                "+update",
                "--doc",
                node.obj_token,
                "--command",
                "overwrite",
                "--doc-format",
                "markdown",
                "--content",
                f"@./{source.name}",
                "--as",
                self.config.identity,
            ],
            cwd=workdir,
        )
        document = data.get("document") or data
        result = data.get("result", "success")
        if result not in {"success", "partial_success"}:
            raise LarkError(f"document update did not succeed: {data}")
        return str(document.get("revision_id", ""))

    def send_dm(self, markdown: str, idempotency_key: str) -> None:
        self.call(
            [
                "im",
                "+messages-send",
                "--user-id",
                self.config.receiver_open_id,
                "--markdown",
                markdown,
                "--idempotency-key",
                idempotency_key[:50],
                "--as",
                self.config.identity,
            ]
        )

    def _node(self, title: str, row: dict[str, Any]) -> PublishNode:
        node_token = row.get("node_token") or row.get("wiki_token")
        obj_token = row.get("obj_token") or row.get("document_id")
        if not node_token or not obj_token:
            raise LarkError(f"missing node/object token: {row}")
        return PublishNode(
            key=_safe_key(title),
            title=title,
            node_token=str(node_token),
            obj_token=str(obj_token),
            url=f"{self.config.wiki_base_url.rstrip('/')}/{node_token}",
        )


class LarkPublisher:
    def __init__(self, config: LarkConfig):
        self.config = config
        self.cli = LarkCLI(config)

    def publish(self, run_dir: Path, status: str) -> PublishManifest:
        publish_root = run_dir / "05_publish"
        publish_root.mkdir(parents=True, exist_ok=True)
        manifest_path = publish_root / "publish_manifest.json"
        if manifest_path.exists():
            manifest = PublishManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if manifest.status == "success" and manifest.dm_sent:
                return manifest
        else:
            manifest = PublishManifest(run_id=run_dir.name)

        if not self.config.space_id or not self.config.receiver_open_id:
            raise LarkError("lark.space_id and lark.receiver_open_id are required")
        date = run_dir.parent.name
        year, month, _ = date.split("-")
        try:
            year_node = self.cli.ensure_node(year)
            manifest.nodes["year"] = year_node
            month_node = self.cli.ensure_node(f"{year}-{month}", year_node.node_token)
            manifest.nodes["month"] = month_node
            day_node = self.cli.ensure_node(
                f"{date} · AI Intelligence Brief", month_node.node_token
            )
            manifest.nodes["day"] = day_node

            successes = json.loads(
                (run_dir / "03_research" / "successes.json").read_text(encoding="utf-8")
            )
            report_urls: dict[str, str] = {}
            for bundle_id, report_path in successes.items():
                content = Path(report_path).read_text(encoding="utf-8")
                title = _markdown_title(content) or bundle_id
                digest = hashlib.sha256(content.encode()).hexdigest()
                manifest_key = f"report:{bundle_id}"
                node = manifest.nodes.get(manifest_key)
                if node is None:
                    node = self.cli.ensure_node(title, day_node.node_token)
                    node.key = bundle_id
                if node.content_hash != digest or node.status != "written":
                    self.cli.write_markdown(node, content, publish_root)
                    node.content_hash = digest
                    node.status = "written"
                manifest.nodes[manifest_key] = node
                report_urls[bundle_id] = node.url or ""
                atomic_write_json(manifest_path, manifest.model_dump(mode="json"))

            brief = (run_dir / "04_brief" / "daily_brief.md").read_text(encoding="utf-8")
            for bundle_id, url in report_urls.items():
                brief = brief.replace(f"report://{bundle_id}", url)
            brief_hash = hashlib.sha256(brief.encode()).hexdigest()
            self.cli.write_markdown(day_node, brief, publish_root)
            day_node.content_hash = brief_hash
            day_node.status = "written"
            manifest.nodes["day"] = day_node

            idempotency_key = hashlib.sha256(f"ai-digest:{date}".encode()).hexdigest()[:48]
            manifest.dm_idempotency_key = idempotency_key
            if not manifest.dm_sent:
                failures = json.loads(
                    (run_dir / "03_research" / "failures.json").read_text(encoding="utf-8")
                )
                watch_count = sum(
                    1
                    for line in (run_dir / "04_brief" / "watch.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line
                )
                message = (
                    f"## AI Intelligence Radar · {date}\n\n"
                    f"状态：**{status}**  \n"
                    f"研究报告：{len(successes)}，失败：{len(failures)}，Watch：{watch_count}  \n"
                    f"[打开今日 Brief]({day_node.url})"
                )
                self.cli.send_dm(message, idempotency_key)
                manifest.dm_sent = True
            manifest.status = "success"
        except Exception as error:
            manifest.status = "failed"
            manifest.errors.append(f"{type(error).__name__}: {error}")
            atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
            raise
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest


def _safe_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _markdown_title(content: str) -> str | None:
    return next(
        (line.removeprefix("# ").strip() for line in content.splitlines() if line.startswith("# ")),
        None,
    )


def _extract_envelope(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    starts = [0]
    starts.extend(index + 1 for index, character in enumerate(text) if character == "\n")
    for start in starts:
        if text[start : start + 1] != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    raise json.JSONDecodeError("no lark envelope found", text, 0)
