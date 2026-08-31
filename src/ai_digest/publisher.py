from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from .config import LarkConfig, resolve_binary
from .models import PublishManifest, PublishNode, ResearchArtifactManifest
from .store import parse_jsonl_text
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


class LarkPublisher:
    NAVIGATION_VERSION = 1

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
            brief_subreport_urls: dict[str, dict[str, str]] = {}
            for bundle_id, report_path in successes.items():
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(bundle_id)):
                    raise LarkError(
                        f"unsafe research report mapping: {bundle_id} -> {report_path}"
                    )
                is_v3 = str(report_path) == f"{bundle_id}/dossier.md"
                if not is_v3 and str(report_path) != f"{bundle_id}/report.md":
                    raise LarkError(
                        f"unsafe research report mapping: {bundle_id} -> {report_path}"
                    )
                source = (
                    run_dir
                    / "03_research"
                    / str(bundle_id)
                    / ("dossier.md" if is_v3 else "report.md")
                )
                if source.is_symlink() or not source.is_file():
                    raise LarkError(f"research report is not a regular file: {bundle_id}")
                content = source.read_text(encoding="utf-8")
                title = _markdown_title(content) or bundle_id
                manifest_key = f"report:{bundle_id}"
                node = manifest.nodes.get(manifest_key)
                if node is None:
                    node = self.cli.ensure_node(
                        f"{title} [{bundle_id}]", day_node.node_token
                    )
                    node.key = bundle_id
                if is_v3:
                    subreport_urls: dict[str, str] = {}
                    subreport_labels: dict[str, str] = {}
                    artifact = json.loads(
                        (source.parent / "research_manifest.json").read_text(encoding="utf-8")
                    )
                    for subreport in artifact.get("subreports", []):
                        relative = (
                            subreport.get("path") if isinstance(subreport, dict) else subreport
                        )
                        if not re.fullmatch(
                            r"subreports/[a-z0-9][a-z0-9_-]{0,79}\.md", str(relative)
                        ):
                            raise LarkError(f"unsafe subreport path: {relative}")
                        child_source = source.parent / str(relative)
                        if child_source.is_symlink() or not child_source.is_file():
                            raise LarkError(f"missing subreport: {relative}")
                        child_content = child_source.read_text(encoding="utf-8")
                        child_slug = Path(str(relative)).stem
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
                                ("研究档案", node.url),
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
                    content = _rewrite_subreport_links(content, str(bundle_id), subreport_urls)
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
            brief = _page_breadcrumb(
                [(year, year_node.url), (f"{year}-{month}", month_node.url)]
            ) + brief
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
                sent = self.cli.send_dm(message, idempotency_key)
                message_id = str(sent.get("message_id") or "")
                chat_id = str(sent.get("chat_id") or "")
                if not message_id or not chat_id:
                    raise LarkError("Lark DM returned no message_id/chat_id")
                manifest.dm_sent = True
                manifest.dm_identity = self.config.dm_identity
                manifest.dm_message_id = message_id
                manifest.dm_chat_id = chat_id
            manifest.artifact_hash = artifact_hash
            manifest.navigation_version = self.NAVIGATION_VERSION
            manifest.status = "success"
        except Exception as error:
            manifest.status = "failed"
            manifest.errors.append(f"{type(error).__name__}: {error}")
            atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
            raise
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest

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
            f"{year_node.title} · AI Intelligence Radar",
            "月份",
            month_children,
        )
        month_content = _navigation_index(
            f"{month_node.title} · 日报索引",
            "日报",
            day_children,
        )
        self.cli.write_markdown(
            year_node,
            year_content,
            publish_root,
            required_substrings=[month_node.url] if month_node.url else [],
        )
        self.cli.write_markdown(
            month_node,
            month_content,
            publish_root,
            required_substrings=[day_node.url] if day_node.url else [],
        )
        for node, content, key in (
            (year_node, year_content, "year"),
            (month_node, month_content, "month"),
        ):
            node.content_hash = hashlib.sha256(content.encode()).hexdigest()
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
    known_reports: set[str] = set()
    preflight_subreports: dict[str, dict[str, str]] = {}
    content_keys = {"year", "month", "day"}
    subreport_count = 0
    for package_id_value, report_path_value in successes.items():
        package_id = str(package_id_value)
        report_path = str(report_path_value)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", package_id):
            raise LarkError(f"unsafe research report id: {package_id}")
        is_v3 = report_path == f"{package_id}/dossier.md"
        if not is_v3 and report_path != f"{package_id}/report.md":
            raise LarkError(f"unsafe research report mapping: {package_id} -> {report_path}")
        report_name = "dossier.md" if is_v3 else "report.md"
        report = _read_regular_text(
            run_dir / "03_research" / package_id / report_name
        )
        known_reports.add(package_id)
        content_keys.add(f"report:{package_id}")
        if not is_v3:
            _assert_no_internal_links(report)
            continue
        artifact_value = _read_regular_json(
            run_dir / "03_research" / package_id / "research_manifest.json"
        )
        artifact = ResearchArtifactManifest.model_validate(artifact_value)
        if artifact.package_id != package_id or artifact.dossier != "dossier.md":
            raise LarkError(f"research manifest mismatch: {package_id}")
        seen_slugs: set[str] = set()
        package_subreports: dict[str, str] = {}
        for subreport in artifact.subreports:
            if isinstance(subreport, str):
                relative = subreport
                slug = Path(subreport).stem
            else:
                relative = subreport.path
                slug = subreport.slug
            if relative != f"subreports/{slug}.md" or not re.fullmatch(
                r"[a-z0-9][a-z0-9_-]{0,79}", slug
            ):
                raise LarkError(f"unsafe subreport path: {package_id}/{relative}")
            if slug in seen_slugs:
                raise LarkError(f"duplicate subreport slug: {package_id}/{slug}")
            seen_slugs.add(slug)
            _read_regular_text(run_dir / "03_research" / package_id / relative)
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
    successes_path = run_dir / "03_research" / "successes.json"
    if successes_path.exists():
        successes = json.loads(successes_path.read_text(encoding="utf-8"))
        for bundle_id, report_path in sorted(successes.items()):
            if str(report_path) == f"{bundle_id}/report.md":
                relative_paths.append(Path("03_research") / str(bundle_id) / "report.md")
            elif str(report_path) == f"{bundle_id}/dossier.md":
                package_root = Path("03_research") / str(bundle_id)
                relative_paths.extend(
                    [package_root / "dossier.md", package_root / "research_manifest.json"]
                )
                manifest_path = run_dir / package_root / "research_manifest.json"
                if manifest_path.exists():
                    artifact = json.loads(manifest_path.read_text(encoding="utf-8"))
                    relative_paths.extend(
                        package_root
                        / str(value.get("path") if isinstance(value, dict) else value)
                        for value in artifact.get("subreports", [])
                    )
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
        raise LarkError(f"dossier references an unknown subreport in {package_id}")
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
