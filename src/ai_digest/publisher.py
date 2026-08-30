from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from .config import LarkConfig, resolve_binary
from .models import PublishManifest, PublishNode
from .utils import atomic_write_json, atomic_write_text


class LarkError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LarkCLI:
    def __init__(self, config: LarkConfig):
        self.config = config
        self.binary = resolve_binary(config.binary)

    def call(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        delays = (0.0, 1.0, 3.0) if retry else (0.0,)
        last_error: LarkError | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                time.sleep(delay)
            try:
                process = subprocess.run(
                    [self.binary, *args],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired as error:
                last_error = LarkError("lark-cli timed out", retryable=True)
                if attempt < len(delays):
                    continue
                raise last_error from error
            try:
                envelope = _extract_envelope(
                    "\n".join(value for value in (process.stdout, process.stderr) if value)
                )
            except json.JSONDecodeError as error:
                last_error = LarkError(
                    f"lark-cli returned non-JSON (exit {process.returncode}): "
                    f"{process.stderr[-1000:]}",
                    retryable=False,
                )
                raise last_error from error
            if process.returncode == 0 and envelope.get("ok") is True:
                return envelope.get("data") or {}
            detail = envelope.get("error") or envelope
            last_error = LarkError(
                f"lark-cli failed: {detail}", retryable=_retryable_lark_error(detail)
            )
            if not last_error.retryable or attempt == len(delays):
                raise last_error
        raise last_error or LarkError("lark-cli failed without an error response")

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
        args = ["wiki", "+node-create", "--title", title, "--obj-type", "docx"]
        if parent_token:
            args.extend(["--parent-node-token", parent_token])
        else:
            args.extend(["--space-id", self.config.space_id])
        args.extend(["--as", self.config.identity])
        for attempt, delay in enumerate((0.0, 1.0, 3.0), start=1):
            if delay:
                time.sleep(delay)
            for row in self.list_nodes(parent_token):
                if row.get("title") == title:
                    return self._node(title, row)
            try:
                data = self.call(args, retry=False)
                return self._node(title, data)
            except LarkError as error:
                if not error.retryable or attempt == 3:
                    raise
        raise LarkError(f"could not create or recover wiki node: {title}")

    def write_markdown(
        self,
        node: PublishNode,
        content: str,
        workdir: Path,
        *,
        required_substrings: list[str] | None = None,
    ) -> str:
        fingerprint = hashlib.sha256(content.encode()).hexdigest()[:20]
        rendered = (
            content.rstrip()
            + "\n\n---\n\n"
            + f"_AI Digest verification: `{fingerprint}`_\n"
        )
        source = workdir / f"lark-{node.key}.md"
        atomic_write_text(source, rendered)
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
        if result != "success" or data.get("warnings"):
            raise LarkError(f"document update was not complete: {data}")
        fetched = self.call(
            [
                "docs",
                "+fetch",
                "--doc",
                node.obj_token,
                "--doc-format",
                "markdown",
                "--detail",
                "simple",
                "--as",
                self.config.identity,
            ]
        )
        readback = str((fetched.get("document") or fetched).get("content", ""))
        required = [fingerprint, *(required_substrings or [])]
        missing = [value for value in required if value not in readback]
        if missing:
            raise LarkError(f"document readback verification failed; missing {missing}")
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

    def delete_node(self, node: PublishNode) -> None:
        self.call(
            [
                "wiki",
                "+node-delete",
                "--node-token",
                node.node_token,
                "--obj-type",
                "wiki",
                "--space-id",
                self.config.space_id,
                "--yes",
                "--as",
                self.config.identity,
            ],
            retry=False,
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
            run_manifest = json.loads(
                (run_dir / "00_run_manifest.json").read_text(encoding="utf-8")
            )
            manifest = PublishManifest(run_id=str(run_manifest["run_id"]))

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
                if (
                    not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(bundle_id))
                    or str(report_path) != f"{bundle_id}/report.md"
                ):
                    raise LarkError(
                        f"unsafe research report mapping: {bundle_id} -> {report_path}"
                    )
                source = run_dir / "03_research" / str(bundle_id) / "report.md"
                if source.is_symlink() or not source.is_file():
                    raise LarkError(f"research report is not a regular file: {bundle_id}")
                content = source.read_text(encoding="utf-8")
                title = _markdown_title(content) or bundle_id
                digest = hashlib.sha256(content.encode()).hexdigest()
                manifest_key = f"report:{bundle_id}"
                node = manifest.nodes.get(manifest_key)
                if node is None:
                    node = self.cli.ensure_node(
                        f"{title} [{bundle_id}]", day_node.node_token
                    )
                    node.key = bundle_id
                if node.content_hash != digest or node.status != "written":
                    self.cli.write_markdown(node, content, publish_root)
                    node.content_hash = digest
                    node.status = "written"
                manifest.nodes[manifest_key] = node
                report_urls[bundle_id] = node.url or ""
                atomic_write_json(manifest_path, manifest.model_dump(mode="json"))

            brief = (run_dir / "04_brief" / "daily_brief.md").read_text(encoding="utf-8")
            brief = _rewrite_report_links(brief, report_urls)
            brief_hash = hashlib.sha256(brief.encode()).hexdigest()
            self.cli.write_markdown(
                day_node,
                brief,
                publish_root,
                required_substrings=[url for url in report_urls.values() if url],
            )
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
                source_health = json.loads(
                    (run_dir / "01_phase1" / "source_health.json").read_text(
                        encoding="utf-8"
                    )
                )
                disabled = [
                    name
                    for name, value in source_health.items()
                    if value.get("status") == "disabled"
                ]
                source_issues = [
                    name
                    for name, value in source_health.items()
                    if value.get("status") in {"partial", "failed"}
                ]
                message = (
                    f"## AI Intelligence Radar · {date}\n\n"
                    f"状态：**{status}**  \n"
                    f"研究报告：{len(successes)}，失败：{len(failures)}，Watch：{watch_count}  \n"
                    f"停用来源：{', '.join(disabled) if disabled else '无'}  \n"
                    f"异常来源：{', '.join(source_issues) if source_issues else '无'}  \n"
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


def _rewrite_report_links(content: str, report_urls: dict[str, str]) -> str:
    if not report_urls:
        return content
    identifiers = sorted(report_urls, key=len, reverse=True)
    pattern = re.compile(
        r"report://(" + "|".join(re.escape(value) for value in identifiers) + r")(?![a-z0-9_-])"
    )
    found: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        bundle_id = match.group(1)
        found.add(bundle_id)
        return report_urls[bundle_id]

    rewritten = pattern.sub(replace, content)
    missing = set(report_urls) - found
    if missing:
        raise LarkError(f"brief is missing report links for: {sorted(missing)}")
    return rewritten


def _retryable_lark_error(detail: object) -> bool:
    text = json.dumps(detail, ensure_ascii=False).lower()
    transient = (
        "rate_limit",
        "rate limit",
        "timeout",
        "timed out",
        "network",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "connection reset",
    )
    return any(value in text for value in transient)


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
