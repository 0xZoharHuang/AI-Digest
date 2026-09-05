from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from .artifacts import load_artifact_layout, load_not_published_artifacts
from .config import LarkConfig, resolve_binary
from .models import (
    Phase2ResearchObject,
    Phase3Admission,
    PublishManifest,
    PublishNode,
    ResearchArtifactManifest,
    ResearchPackage,
)
from .store import parse_jsonl_text
from .utils import atomic_write_json, atomic_write_text


class LarkError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


NOTIFICATION_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)


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

    def send_dm(self, markdown: str, idempotency_key: str) -> dict[str, Any]:
        return self.call(
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
                self.config.dm_identity,
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


def notify_run_failure(
    config: LarkConfig,
    run_dir: Path,
    *,
    phase: str,
    detail: str,
) -> dict[str, Any]:
    return notify_run_issue(
        config,
        run_dir,
        status="FAILED",
        phase=phase,
        detail=detail,
    )


def notify_run_issue(
    config: LarkConfig,
    run_dir: Path,
    *,
    status: Literal["FAILED", "RETRYING"],
    phase: str,
    detail: str,
) -> dict[str, Any]:
    """Send an operational alert even when the content tree is not publishable."""

    if not config.receiver_open_id:
        raise LarkError("lark.receiver_open_id is required for run notifications")
    run_manifest = _read_regular_json(run_dir / "00_run_manifest.json")
    run_id = str(run_manifest.get("run_id") or "")
    date = str(run_manifest.get("date") or run_dir.parent.name)
    if not run_id:
        raise LarkError("run manifest has no run_id for failure notification")
    safe_detail = " ".join(detail.replace("`", "'").split())[:1200]
    event_hash = hashlib.sha256(
        f"{run_id}\0{status}\0{phase}\0{safe_detail}".encode()
    ).hexdigest()
    idempotency_key = hashlib.sha256(
        f"ai-digest-alert:{config.dm_identity}:{event_hash}".encode()
    ).hexdigest()[:48]
    message = (
        f"## AI Intelligence Radar · {date}\n\n"
        f"状态：**{status}**  \n"
        f"当前阶段：**{phase}**  \n"
        f"运行 ID：`{run_id}`  \n"
        f"说明：{safe_detail or '没有可用错误详情。'}\n\n"
        + (
            "任务已保留并会按退避策略重试。"
            if status == "RETRYING"
            else "内容发布可能未完成，请以这条运行通知为准。"
        )
    )
    return _send_notification_with_receipt(
        LarkCLI(config),
        run_dir,
        kind="retrying" if status == "RETRYING" else "failure",
        markdown=message,
        idempotency_key=idempotency_key,
    )


def _send_notification_with_receipt(
    cli: LarkCLI,
    run_dir: Path,
    *,
    kind: str,
    markdown: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", kind):
        raise LarkError(f"unsafe notification kind: {kind}")
    notifications = run_dir / "05_publish" / "notifications"
    receipt_path = notifications / f"{kind}-{idempotency_key[:16]}.json"
    cached: dict[str, Any] = {}
    if receipt_path.is_file() and not receipt_path.is_symlink():
        cached = cast(
            dict[str, Any],
            json.loads(receipt_path.read_text(encoding="utf-8")),
        )
        if (
            cached.get("status") == "sent"
            and cached.get("idempotency_key") == idempotency_key
            and cached.get("message_id")
            and cached.get("chat_id")
        ):
            return cached
    attempt = max(0, int(cached.get("attempt") or 0)) + 1
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "status": "pending",
        "idempotency_key": idempotency_key,
        "attempt": attempt,
        "markdown": markdown,
    }
    atomic_write_json(receipt_path, receipt)
    try:
        sent = cli.send_dm(markdown, idempotency_key)
        message_id = str(sent.get("message_id") or "")
        chat_id = str(sent.get("chat_id") or "")
        if not message_id or not chat_id:
            raise LarkError("Lark notification returned no message_id/chat_id")
    except Exception as error:
        receipt.update(
            {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}"[-4000:],
                "next_retry_at": (
                    datetime.now(UTC)
                    + NOTIFICATION_RETRY_DELAYS[
                        min(attempt - 1, len(NOTIFICATION_RETRY_DELAYS) - 1)
                    ]
                ).isoformat(),
            }
        )
        atomic_write_json(receipt_path, receipt)
        raise
    receipt.update(
        {
            "status": "sent",
            "message_id": message_id,
            "chat_id": chat_id,
        }
    )
    atomic_write_json(receipt_path, receipt)
    return receipt


def retry_pending_notifications(
    config: LarkConfig,
    runtime_root: Path,
    shared_runtime_root: Path,
    *,
    now: datetime | None = None,
) -> list[Path]:
    if not config.receiver_open_id:
        return []
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    candidates = list(
        runtime_root.glob("runs/*/attempt-*/05_publish/notifications/*.json")
    )
    for queue_name in ("jobs", "retry_wait", "completed", "publish_pending", "failed"):
        candidates.extend(
            (shared_runtime_root / queue_name).glob(
                "*/05_publish/notifications/*.json"
            )
        )
    sent: list[Path] = []
    for receipt_path in sorted(set(candidates)):
        if receipt_path.is_symlink() or not receipt_path.is_file():
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or receipt.get("status") == "sent":
                continue
            retry_at_value = receipt.get("next_retry_at")
            if retry_at_value and datetime.fromisoformat(str(retry_at_value)).astimezone(
                UTC
            ) > moment:
                continue
            kind = str(receipt["kind"])
            markdown = str(receipt["markdown"])
            idempotency_key = str(receipt["idempotency_key"])
            run_dir = receipt_path.parents[2]
            _send_notification_with_receipt(
                LarkCLI(config),
                run_dir,
                kind=kind,
                markdown=markdown,
                idempotency_key=idempotency_key,
            )
        except Exception:
            continue
        sent.append(receipt_path)
    return sent

class LarkPublisher:
    NAVIGATION_VERSION = 3

    def __init__(self, config: LarkConfig):
        self.config = config
        self.cli = LarkCLI(config)

    def publish(self, run_dir: Path, status: str) -> PublishManifest:
        preflight = validate_publish_inputs(run_dir, status)
        publish_root = run_dir / "05_publish"
        publish_root.mkdir(parents=True, exist_ok=True)
        manifest_path = publish_root / "publish_manifest.json"
        artifact_hash = str(preflight["artifact_hash"])
        expected_content_keys = {str(value) for value in preflight["content_keys"]}
        if manifest_path.exists():
            manifest = PublishManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if (
                manifest.status == "success"
                and manifest.dm_sent
                and manifest.dm_message_id
                and manifest.artifact_hash == artifact_hash
                and manifest.navigation_version >= self.NAVIGATION_VERSION
                and not {
                    key
                    for key in manifest.nodes
                    if (key.startswith("report:") or key.startswith("subreport:"))
                    and key not in expected_content_keys
                }
            ):
                return manifest
            if manifest.artifact_hash != artifact_hash:
                manifest.status = "pending"
                manifest.dm_sent = False
                manifest.dm_message_id = None
                manifest.dm_chat_id = None
                manifest.errors = []
            if manifest.dm_sent and not manifest.dm_message_id:
                manifest.dm_sent = False
        else:
            run_manifest = json.loads(
                (run_dir / "00_run_manifest.json").read_text(encoding="utf-8")
            )
            manifest = PublishManifest(run_id=str(run_manifest["run_id"]))

        if not self.config.space_id or not self.config.receiver_open_id:
            raise LarkError("lark.space_id and lark.receiver_open_id are required")
        date = run_dir.parent.name
        year, month, _ = date.split("-")
        year_title = f"{year} · AI Intelligence Radar"
        month_title = f"{year}-{month} · 日报索引"
        day_title = f"{date} · AI Intelligence Brief"
        try:
            year_node = self._ensure_cached_node(
                year_title,
                None,
                manifest.nodes.get("year"),
            )
            manifest.nodes["year"] = year_node
            month_node = self._ensure_cached_node(
                month_title,
                year_node.node_token,
                manifest.nodes.get("month"),
            )
            manifest.nodes["month"] = month_node
            day_node = self._ensure_cached_node(
                day_title,
                month_node.node_token,
                manifest.nodes.get("day"),
            )
            manifest.nodes["day"] = day_node

            successes = json.loads(
                (run_dir / "03_research" / "successes.json").read_text(encoding="utf-8")
            )
            report_urls: dict[str, str] = {}
            brief_subreport_urls: dict[str, dict[str, str]] = {}
            for bundle_id, report_path in successes.items():
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(bundle_id)):
                    raise LarkError(
                        f"unsafe research report mapping: {bundle_id} -> {report_path}"
                    )
                package_root = run_dir / "03_research" / str(bundle_id)
                try:
                    layout = load_artifact_layout(
                        package_root,
                        str(bundle_id),
                        str(report_path),
                    )
                except (ValueError, TypeError) as error:
                    raise LarkError(str(error)) from error
                source = layout.main_path
                if source.is_symlink() or not source.is_file():
                    raise LarkError(f"research report is not a regular file: {bundle_id}")
                content = source.read_text(encoding="utf-8")
                title = _markdown_title(content) or bundle_id
                manifest_key = f"report:{bundle_id}"
                node = manifest.nodes.get(manifest_key)
                if node is None:
                    node_title = (
                        title
                        if layout.kind == "main_report_v1"
                        else f"{title} [{bundle_id}]"
                    )
                    node = self.cli.ensure_node(node_title, day_node.node_token)
                    node.key = bundle_id
                if layout.subreports:
                    subreport_urls: dict[str, str] = {}
                    subreport_labels: dict[str, str] = {}
                    for subreport in layout.subreports:
                        relative = subreport.path
                        child_source = package_root / relative
                        child_content = child_source.read_text(encoding="utf-8")
                        child_slug = subreport.slug
                        child_key = f"subreport:{bundle_id}:{child_slug}"
                        child = manifest.nodes.get(child_key)
                        if child is None:
                            child = self.cli.ensure_node(
                                _markdown_title(child_content) or child_slug,
                                node.node_token,
                            )
                            child.key = child_slug
                        child_content = _page_breadcrumb(
                            [
                                ("当日日报", day_node.url),
                                ("主报告", node.url),
                            ]
                        ) + child_content
                        child_hash = hashlib.sha256(child_content.encode()).hexdigest()
                        if child.content_hash != child_hash or child.status != "written":
                            self.cli.write_markdown(child, child_content, publish_root)
                            child.content_hash = child_hash
                            child.status = "written"
                        manifest.nodes[child_key] = child
                        expected_content_keys.add(child_key)
                        subreport_urls[child_slug] = child.url or ""
                        subreport_labels[child_slug] = child.title
                    content = _rewrite_subreport_links(
                        content, str(bundle_id), subreport_urls
                    )
                    content = _append_subreport_index(
                        content,
                        subreport_urls,
                        subreport_labels,
                    )
                    brief_subreport_urls[str(bundle_id)] = subreport_urls
                content = _page_breadcrumb(
                    [
                        (f"{year}-{month}", month_node.url),
                        ("当日日报", day_node.url),
                    ]
                ) + content
                digest = hashlib.sha256(content.encode()).hexdigest()
                if node.content_hash != digest or node.status != "written":
                    self.cli.write_markdown(node, content, publish_root)
                    node.content_hash = digest
                    node.status = "written"
                manifest.nodes[manifest_key] = node
                expected_content_keys.add(manifest_key)
                report_urls[bundle_id] = node.url or ""
                atomic_write_json(manifest_path, manifest.model_dump(mode="json"))

            brief = (run_dir / "04_brief" / "daily_brief.md").read_text(encoding="utf-8")
            brief = _rewrite_report_links(brief, report_urls)
            for package_id, subreport_urls in brief_subreport_urls.items():
                brief = _rewrite_subreport_links(brief, package_id, subreport_urls)
            _assert_no_internal_links(brief)
            brief = _replace_markdown_title(brief, day_title)
            brief = _page_breadcrumb(
                [(year, year_node.url), (f"{year}-{month}", month_node.url)]
            ) + brief
            brief_hash = hashlib.sha256(brief.encode()).hexdigest()
            if day_node.content_hash != brief_hash or day_node.status != "written":
                self.cli.write_markdown(
                    day_node,
                    brief,
                    publish_root,
                    required_substrings=[url for url in report_urls.values() if url],
                )
            day_node.content_hash = brief_hash
            day_node.status = "written"
            manifest.nodes["day"] = day_node

            self._delete_stale_content_nodes(manifest, expected_content_keys)
            self._write_navigation_indexes(
                manifest,
                publish_root,
                year_node,
                month_node,
                day_node,
            )

            idempotency_key = hashlib.sha256(
                f"ai-digest:{self.config.dm_identity}:{date}:{artifact_hash}".encode()
            ).hexdigest()[:48]
            manifest.dm_idempotency_key = idempotency_key
            if not manifest.dm_sent:
                failures = json.loads(
                    (run_dir / "03_research" / "failures.json").read_text(encoding="utf-8")
                )
                watch_count = int(preflight["watch_count"])
                not_published_count = int(preflight["not_published_count"])
                research_object_count = int(preflight["research_object_count"])
                scheduled_research_count = int(
                    preflight["scheduled_research_count"]
                )
                not_scheduled_research_count = int(
                    preflight["not_scheduled_research_count"]
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
                    f"研究报告：{len(successes)}，核查后未发布：{not_published_count}，"
                    f"失败：{len(failures)}，Watch：{watch_count}  \n"
                    f"Phase 2 候选信息包：{research_object_count}，"
                    f"当日已调度：{scheduled_research_count}，"
                    f"未调度：{not_scheduled_research_count}  \n"
                    f"停用来源：{', '.join(disabled) if disabled else '无'}  \n"
                    f"异常来源：{', '.join(source_issues) if source_issues else '无'}  \n"
                    f"[打开今日 Brief]({day_node.url})"
                )
                sent = _send_notification_with_receipt(
                    self.cli,
                    run_dir,
                    kind="daily_result",
                    markdown=message,
                    idempotency_key=idempotency_key,
                )
                message_id = str(sent["message_id"])
                chat_id = str(sent["chat_id"])
                manifest.dm_sent = True
                manifest.dm_identity = self.config.dm_identity
                manifest.dm_message_id = message_id
                manifest.dm_chat_id = chat_id
            manifest.artifact_hash = artifact_hash
            manifest.navigation_version = self.NAVIGATION_VERSION
            manifest.errors = []
            manifest.status = "success"
        except Exception as error:
            manifest.status = "failed"
            message = f"{type(error).__name__}: {error}"
            if not manifest.errors or manifest.errors[-1] != message:
                manifest.errors.append(message)
            manifest.errors = manifest.errors[-20:]
            atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
            raise
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest

    def _ensure_cached_node(
        self,
        title: str,
        parent_token: str | None,
        cached: PublishNode | None,
    ) -> PublishNode:
        """Reuse a known node even when a prior Markdown import changed its title."""

        if cached is not None:
            for row in self.cli.list_nodes(parent_token):
                node_token = str(row.get("node_token") or row.get("wiki_token") or "")
                obj_token = str(row.get("obj_token") or row.get("document_id") or "")
                if node_token == cached.node_token and obj_token == cached.obj_token:
                    return cached.model_copy(update={"title": title})
        return _restore_node_state(self.cli.ensure_node(title, parent_token), cached)

    def _delete_stale_content_nodes(
        self,
        manifest: PublishManifest,
        expected_keys: set[str],
    ) -> None:
        stale = [
            key
            for key in manifest.nodes
            if key not in expected_keys
            and (key.startswith("report:") or key.startswith("subreport:"))
        ]
        for key in sorted(stale, key=lambda value: (not value.startswith("subreport:"), value)):
            self.cli.delete_node(manifest.nodes[key])
            del manifest.nodes[key]

    def _write_navigation_indexes(
        self,
        manifest: PublishManifest,
        publish_root: Path,
        year_node: PublishNode,
        month_node: PublishNode,
        day_node: PublishNode,
    ) -> None:
        month_children = _navigation_children(
            self.cli.list_nodes(year_node.node_token), self.config.wiki_base_url
        )
        day_children = _navigation_children(
            self.cli.list_nodes(month_node.node_token), self.config.wiki_base_url
        )
        year_content = _navigation_index(
            year_node.title,
            "月份",
            month_children,
        )
        month_content = _navigation_index(
            month_node.title,
            "日报",
            day_children,
        )
        for node, content, key in (
            (year_node, year_content, "year"),
            (month_node, month_content, "month"),
        ):
            digest = hashlib.sha256(content.encode()).hexdigest()
            if node.content_hash != digest or node.status != "written":
                required = (
                    [month_node.url]
                    if key == "year" and month_node.url
                    else [day_node.url]
                    if key == "month" and day_node.url
                    else []
                )
                self.cli.write_markdown(
                    node,
                    content,
                    publish_root,
                    required_substrings=required,
                )
            node.content_hash = digest
            node.status = "written"
            manifest.nodes[key] = node


def validate_publish_inputs(run_dir: Path, status: str) -> dict[str, Any]:
    """Validate the complete publish tree before the first external Lark write."""

    run_manifest = _read_regular_json(run_dir / "00_run_manifest.json")
    run_id = str(run_manifest.get("run_id") or "")
    if not run_id:
        raise LarkError("run manifest has no run_id")
    date = run_dir.parent.name
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise LarkError(f"invalid run date directory: {date}")
    if not status:
        raise LarkError("publish status is empty")

    successes = _read_regular_json(run_dir / "03_research" / "successes.json")
    failures = _read_regular_json(run_dir / "03_research" / "failures.json")
    source_health = _read_regular_json(run_dir / "01_phase1" / "source_health.json")
    if not isinstance(successes, dict):
        raise LarkError("successes.json must be an object")
    if not isinstance(failures, list):
        raise LarkError("failures.json must be an array")
    if not isinstance(source_health, dict):
        raise LarkError("source_health.json must be an object")

    brief = _read_regular_text(run_dir / "04_brief" / "daily_brief.md")
    watch = parse_jsonl_text(_read_regular_text(run_dir / "04_brief" / "watch.jsonl"))
    not_published_path = run_dir / "03_research" / "not_published.json"
    not_published = (
        _read_regular_json(not_published_path) if not_published_path.exists() else []
    )
    if not isinstance(not_published, list):
        raise LarkError("not_published.json must be an array")
    known_reports: set[str] = set()
    actual_quality_manifests: dict[str, ResearchArtifactManifest] = {}
    preflight_subreports: dict[str, dict[str, str]] = {}
    content_keys = {"year", "month", "day"}
    subreport_count = 0
    expected_units: dict[str, set[str]] = {}
    formal_research = False
    phase2_unit_ids: set[str] = set()
    available_object_ids: list[str] = []
    admission_required = False
    packages_path = run_dir / "02_routing" / "packages.json"
    objects_path = run_dir / "02_routing" / "objects.json"
    if packages_path.is_file() and (run_dir / "02_routing" / "phase2_manifest.json").is_file():
        formal_research = True
        phase2_manifest = _read_regular_json(run_dir / "02_routing" / "phase2_manifest.json")
        if isinstance(phase2_manifest, dict) and phase2_manifest.get("contract") == "semantic_labels_v1":
            from .phase2_labels import validate_artifacts
            validate_artifacts(run_dir / "02_routing")
            admission_required = True
        packages = [
            ResearchPackage.model_validate(row)
            for row in json.loads(_read_regular_text(packages_path))
        ]
        expected_units = {
            package.package_id: set(package.unit_ids) for package in packages
        }
        available_object_ids = [package.package_id for package in packages]
    elif objects_path.is_file() and (run_dir / "02_routing" / "phase2_manifest.json").is_file():
        formal_research = True
        phase2_manifest = _read_regular_json(
            run_dir / "02_routing" / "phase2_manifest.json"
        )
        admission_required = (
            isinstance(phase2_manifest, dict)
            and phase2_manifest.get("object_order") == "semantic_priority_desc"
        )
        objects = [
            Phase2ResearchObject.model_validate(row)
            for row in json.loads(_read_regular_text(objects_path))
        ]
        expected_units = {value.object_id: set(value.unit_ids) for value in objects}
        available_object_ids = [value.object_id for value in objects]
    units_path = run_dir / "02_routing" / "units.jsonl"
    if formal_research:
        phase2_unit_ids = {
            str(value.get("unit_id") or "")
            for value in parse_jsonl_text(_read_regular_text(units_path))
        }
        if "" in phase2_unit_ids:
            raise LarkError("Phase 2 units contain an empty unit id")
        admission_path = run_dir / "03_research" / "phase3_admission.json"
        if admission_required or admission_path.is_file():
            if not admission_path.is_file() or admission_path.is_symlink():
                raise LarkError("formal Phase 3 admission is missing or unsafe")
            admission = Phase3Admission.model_validate_json(
                _read_regular_text(admission_path)
            )
            if admission.available_object_ids != available_object_ids:
                raise LarkError("Phase 3 admission does not preserve Phase 2 object order")
            selected_ids = set(admission.selected_object_ids)
            expected_units = {
                key: value for key, value in expected_units.items() if key in selected_ids
            }
        else:
            admission = None
    else:
        admission = None
    for package_id_value, report_path_value in successes.items():
        package_id = str(package_id_value)
        report_path = str(report_path_value)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", package_id):
            raise LarkError(f"unsafe research report id: {package_id}")
        if expected_units and package_id not in expected_units:
            raise LarkError(f"research report references an unknown package: {package_id}")
        package_root = run_dir / "03_research" / package_id
        try:
            layout = load_artifact_layout(
                package_root,
                package_id,
                report_path,
                expected_unit_ids=expected_units.get(package_id),
            )
        except (ValueError, TypeError) as error:
            raise LarkError(str(error)) from error
        if formal_research and layout.kind != "main_report_v1":
            raise LarkError(
                f"formal Phase 3 object used a legacy report contract: {package_id}"
            )
        if formal_research:
            assert layout.manifest_path is not None
            actual_quality_manifests[package_id] = (
                ResearchArtifactManifest.model_validate_json(
                    _read_regular_text(layout.manifest_path)
                )
            )
        report = _read_regular_text(layout.main_path)
        known_reports.add(package_id)
        content_keys.add(f"report:{package_id}")
        seen_slugs: set[str] = set()
        package_subreports: dict[str, str] = {}
        for subreport in layout.subreports:
            relative = subreport.path
            slug = subreport.slug
            if slug in seen_slugs:
                raise LarkError(f"duplicate subreport slug: {package_id}/{slug}")
            seen_slugs.add(slug)
            _read_regular_text(package_root / relative)
            package_subreports[slug] = f"https://preflight.invalid/{package_id}/{slug}"
            content_keys.add(f"subreport:{package_id}:{slug}")
            subreport_count += 1
        preflight_subreports[package_id] = package_subreports
        rendered_report = _rewrite_subreport_links(
            report,
            package_id,
            package_subreports,
        )
        _assert_no_internal_links(rendered_report)

    if formal_research:
        success_ids = set(str(value) for value in successes)
        not_published_ids = [str(value) for value in not_published]
        failure_ids = [
            str(value.get("package_id") or "")
            for value in failures
            if isinstance(value, dict)
        ]
        outcome_ids = [*success_ids, *not_published_ids, *failure_ids]
        if (
            any(not value for value in failure_ids)
            or len(outcome_ids) != len(set(outcome_ids))
            or set(outcome_ids) != set(expected_units)
        ):
            raise LarkError("formal Phase 3 outcomes do not exactly cover Phase 2 objects")
        for package_id in not_published_ids:
            try:
                manifest_path, _intake, _evidence = load_not_published_artifacts(
                    run_dir / "03_research" / package_id,
                    package_id,
                    expected_unit_ids=expected_units[package_id],
                )
            except (ValueError, TypeError) as error:
                raise LarkError(str(error)) from error
            actual_quality_manifests[package_id] = (
                ResearchArtifactManifest.model_validate_json(
                    _read_regular_text(manifest_path)
                )
            )
        research_quality_path = run_dir / "03_research" / "quality.json"
        if not research_quality_path.is_file() or research_quality_path.is_symlink():
            raise LarkError("formal Phase 3 quality is missing or unsafe")
        research_quality = _read_regular_json(research_quality_path)
        if not isinstance(research_quality, dict):
            raise LarkError("formal Phase 3 quality must be an object")
        expected_research_status = (
            "quiet"
            if not expected_units
            else "partial"
            if failures
            else "success"
        )
        if research_quality.get("status") != expected_research_status:
            raise LarkError("formal Phase 3 quality contradicts research outcomes")
        if admission is not None and research_quality.get(
            "admission"
        ) != admission.model_dump(mode="json"):
            raise LarkError("formal Phase 3 quality contradicts admission")
        quality_rows = research_quality.get("packages")
        if not isinstance(quality_rows, list):
            raise LarkError("formal Phase 3 quality packages must be an array")
        quality_manifests = [
            ResearchArtifactManifest.model_validate(value) for value in quality_rows
        ]
        quality_ids = [value.package_id for value in quality_manifests]
        if len(quality_ids) != len(set(quality_ids)) or set(quality_ids) != (
            success_ids | set(not_published_ids)
        ):
            raise LarkError("formal Phase 3 quality does not match decided artifacts")
        quality_by_id = {value.package_id: value for value in quality_manifests}
        if any(
            quality_by_id[package_id] != manifest
            for package_id, manifest in actual_quality_manifests.items()
        ):
            raise LarkError("formal Phase 3 quality contains stale artifact manifests")

        phase4_quality_path = run_dir / "04_brief" / "quality.json"
        if not phase4_quality_path.is_file() or phase4_quality_path.is_symlink():
            raise LarkError("formal Phase 4 quality is missing or unsafe")
        phase4_quality = _read_regular_json(phase4_quality_path)
        if not isinstance(phase4_quality, dict) or phase4_quality.get(
            "status"
        ) not in {"success", "partial"}:
            raise LarkError("formal Phase 4 quality is missing or invalid")
        required = phase4_quality.get("required_report_ids")
        linked = phase4_quality.get("linked_report_ids")
        missing = phase4_quality.get("missing_report_ids")
        if (
            not isinstance(required, list)
            or not isinstance(linked, list)
            or not isinstance(missing, list)
            or len(required) != len(set(required))
            or set(required) != success_ids
            or set(linked) != success_ids
            or missing
            or phase4_quality.get("watch_count") != len(watch)
        ):
            raise LarkError("formal Phase 4 quality does not match publish inputs")
        if admission is not None and (
            phase4_quality.get("research_object_count")
            != len(admission.available_object_ids)
            or phase4_quality.get("scheduled_research_count")
            != len(admission.selected_object_ids)
            or phase4_quality.get("not_scheduled_research_count")
            != len(admission.not_scheduled_object_ids)
        ):
            raise LarkError("formal Phase 4 admission counts do not match")
        leaked_unit_ids = sorted(
            unit_id for unit_id in phase2_unit_ids if unit_id and unit_id in brief
        )
        if leaked_unit_ids:
            raise LarkError(
                f"daily brief exposes internal Phase 2 unit ids: {leaked_unit_ids[:5]}"
            )

    rendered_brief = _rewrite_report_links(
        brief,
        {
            package_id: f"https://preflight.invalid/report/{package_id}"
            for package_id in known_reports
        },
    )
    for package_id, subreports in preflight_subreports.items():
        rendered_brief = _rewrite_subreport_links(
            rendered_brief,
            package_id,
            subreports,
        )
    _assert_no_internal_links(rendered_brief)

    return {
        "run_id": run_id,
        "date": date,
        "status": status,
        "report_count": len(known_reports),
        "subreport_count": subreport_count,
        "failure_count": len(failures),
        "watch_count": len(watch),
        "not_published_count": len(not_published),
        "research_object_count": (
            len(admission.available_object_ids)
            if admission is not None
            else len(known_reports)
        ),
        "scheduled_research_count": (
            len(admission.selected_object_ids)
            if admission is not None
            else len(known_reports)
        ),
        "not_scheduled_research_count": (
            len(admission.not_scheduled_object_ids) if admission is not None else 0
        ),
        "content_keys": sorted(content_keys),
        "artifact_hash": _publish_artifact_hash(run_dir, status),
    }


def _read_regular_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise LarkError(f"publish input is not a regular file: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip() and path.name not in {"watch.jsonl"}:
        raise LarkError(f"publish input is empty: {path}")
    return content


def _read_regular_json(path: Path) -> Any:
    return json.loads(_read_regular_text(path))


def _assert_no_internal_links(content: str) -> None:
    unresolved = [scheme for scheme in ("report://", "subreport://") if scheme in content]
    if unresolved:
        raise LarkError("unresolved internal links remain: " + ", ".join(unresolved))


def _page_breadcrumb(links: list[tuple[str, str | None]]) -> str:
    values = [f"[{label}]({url})" for label, url in links if url]
    return f"> 导航：{' / '.join(values)}\n\n" if values else ""


def _restore_node_state(node: PublishNode, cached: PublishNode | None) -> PublishNode:
    if (
        cached is not None
        and cached.node_token == node.node_token
        and cached.obj_token == node.obj_token
    ):
        node.content_hash = cached.content_hash
        node.status = cached.status
    return node


def _navigation_children(
    rows: list[dict[str, Any]],
    wiki_base_url: str,
) -> list[tuple[str, str]]:
    children: list[tuple[str, str]] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        token = str(row.get("node_token") or row.get("wiki_token") or "").strip()
        if not title or not token:
            continue
        children.append((title, f"{wiki_base_url.rstrip('/')}/{token}"))
    return sorted(set(children), key=lambda value: value[0], reverse=True)


def _navigation_index(
    title: str,
    section: str,
    children: list[tuple[str, str]],
) -> str:
    lines = [f"# {title}", "", f"## {section}", ""]
    if children:
        lines.extend(f"- [{label}]({url})" for label, url in children)
    else:
        lines.append("- 暂无内容。")
    return "\n".join(lines) + "\n"


def _append_subreport_index(
    content: str,
    urls: dict[str, str],
    labels: dict[str, str],
) -> str:
    if not urls:
        return content
    lines = ["", "", "---", "", "## 子报告导航", ""]
    lines.extend(
        f"- [{labels.get(slug) or slug}]({url})"
        for slug, url in sorted(urls.items())
        if url
    )
    return content.rstrip() + "\n" + "\n".join(lines) + "\n"


def _publish_artifact_hash(run_dir: Path, status: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(status.encode())
    relative_paths = [
        Path("01_phase1/source_health.json"),
        Path("03_research/successes.json"),
        Path("03_research/failures.json"),
        Path("04_brief/watch.jsonl"),
        Path("04_brief/daily_brief.md"),
    ]
    formal_contract = (
        (run_dir / "02_routing" / "phase2_manifest.json").is_file()
        and any(
            (run_dir / "02_routing" / name).is_file()
            for name in ("objects.json", "packages.json")
        )
    )
    if formal_contract:
        relative_paths.extend(
            [
                Path("03_research/not_published.json"),
                Path("03_research/phase3_admission.json"),
                Path("03_research/quality.json"),
                Path("04_brief/quality.json"),
            ]
        )
    successes_path = run_dir / "03_research" / "successes.json"
    if successes_path.exists():
        successes = json.loads(successes_path.read_text(encoding="utf-8"))
        for bundle_id, report_path in sorted(successes.items()):
            package_root = run_dir / "03_research" / str(bundle_id)
            layout = load_artifact_layout(
                package_root,
                str(bundle_id),
                str(report_path),
            )
            relative_paths.extend(path.relative_to(run_dir) for path in layout.files())
    for relative in relative_paths:
        path = run_dir / relative
        hasher.update(relative.as_posix().encode())
        if path.is_file() and not path.is_symlink():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _safe_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _markdown_title(content: str) -> str | None:
    return next(
        (line.removeprefix("# ").strip() for line in content.splitlines() if line.startswith("# ")),
        None,
    )


def _replace_markdown_title(content: str, title: str) -> str:
    """Make the first H1 match the stable Wiki node title."""

    lines = content.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            lines[index] = f"# {title}"
        else:
            lines[index:index] = [f"# {title}", ""]
        break
    else:
        lines = [f"# {title}"]
    suffix = "\n" if content.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _rewrite_report_links(content: str, report_urls: dict[str, str]) -> str:
    if not report_urls:
        return content
    identifiers = sorted(report_urls, key=len, reverse=True)
    pattern = re.compile(
        r"(?<!sub)report://("
        + "|".join(re.escape(value) for value in identifiers)
        + r")(?![a-z0-9_-])"
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


def _rewrite_subreport_links(
    content: str, package_id: str, subreport_urls: dict[str, str]
) -> str:
    for slug in sorted(subreport_urls, key=len, reverse=True):
        url = subreport_urls[slug]
        content = content.replace(f"subreport://{package_id}/{slug}", url)
    if f"subreport://{package_id}/" in content:
        raise LarkError(f"main report references an unknown subreport in {package_id}")
    return content


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
