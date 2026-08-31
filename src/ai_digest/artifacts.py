from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import (
    LegacyResearchArtifactManifest,
    ResearchArtifactManifest,
    ResearchEvidenceEntry,
    ResearchIntakeEntry,
    SubreportArtifact,
)
from .store import parse_jsonl_text


@dataclass(frozen=True)
class ArtifactLayout:
    kind: str
    package_id: str
    main_name: str
    main_path: Path
    subreports: tuple[SubreportArtifact, ...]
    manifest_path: Path | None = None
    intake_path: Path | None = None
    evidence_path: Path | None = None

    def files(self) -> tuple[Path, ...]:
        values = [self.main_path]
        if self.manifest_path is not None:
            values.append(self.manifest_path)
        if self.intake_path is not None:
            values.append(self.intake_path)
        if self.evidence_path is not None:
            values.append(self.evidence_path)
        values.extend(self.main_path.parent / value.path for value in self.subreports)
        return tuple(values)


def report_mapping(package_id: str, report_path: str) -> tuple[str, str]:
    if report_path == f"{package_id}/main_report.md":
        return "main_report_v1", "main_report.md"
    if report_path == f"{package_id}/dossier.md":
        return "legacy_v3", "dossier.md"
    if report_path == f"{package_id}/report.md":
        return "legacy_v2", "report.md"
    raise ValueError(f"unsafe research report mapping: {package_id} -> {report_path}")


def load_artifact_layout(
    package_root: Path,
    package_id: str,
    report_path: str,
    *,
    expected_unit_ids: set[str] | None = None,
) -> ArtifactLayout:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", package_id):
        raise ValueError(f"unsafe research package id: {package_id}")
    kind, main_name = report_mapping(package_id, report_path)
    main_path = package_root / main_name
    _read_regular_text(main_path)
    if kind == "legacy_v2":
        return ArtifactLayout(kind, package_id, main_name, main_path, ())

    manifest_path = package_root / "research_manifest.json"
    manifest_text = _read_regular_text(manifest_path)
    if kind == "legacy_v3":
        legacy_artifact = LegacyResearchArtifactManifest.model_validate_json(
            manifest_text
        )
        if (
            legacy_artifact.package_id != package_id
            or legacy_artifact.dossier != "dossier.md"
        ):
            raise ValueError(f"legacy research manifest mismatch: {package_id}")
        subreports = tuple(
            _normalize_legacy_subreport(value)
            for value in legacy_artifact.subreports
        )
        _validate_subreports(package_root, subreports, None)
        return ArtifactLayout(
            kind,
            package_id,
            main_name,
            main_path,
            subreports,
            manifest_path=manifest_path,
        )

    artifact = ResearchArtifactManifest.model_validate_json(manifest_text)
    if (
        artifact.package_id != package_id
        or artifact.main_report != "main_report.md"
        or artifact.status not in {"success", "complete"}
    ):
        raise ValueError(f"research manifest mismatch: {package_id}")
    intake_path, evidence_path, reviewed_set = _validate_new_ledgers(
        package_root,
        package_id,
        artifact,
        expected_unit_ids,
    )
    subreports = tuple(artifact.subreports)
    _validate_subreports(package_root, subreports, reviewed_set)
    return ArtifactLayout(
        kind,
        package_id,
        main_name,
        main_path,
        subreports,
        manifest_path=manifest_path,
        intake_path=intake_path,
        evidence_path=evidence_path,
    )


def load_not_published_artifacts(
    package_root: Path,
    package_id: str,
    *,
    expected_unit_ids: set[str] | None = None,
) -> tuple[Path, Path, Path]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", package_id):
        raise ValueError(f"unsafe research package id: {package_id}")
    manifest_path = package_root / "research_manifest.json"
    artifact = ResearchArtifactManifest.model_validate_json(
        _read_regular_text(manifest_path)
    )
    if (
        artifact.package_id != package_id
        or artifact.main_report is not None
        or artifact.status != "not_published"
        or artifact.subreports
    ):
        raise ValueError(f"not-published research manifest mismatch: {package_id}")
    intake_path, evidence_path, _ = _validate_new_ledgers(
        package_root,
        package_id,
        artifact,
        expected_unit_ids,
    )
    if (package_root / "main_report.md").exists() or (package_root / "subreports").is_dir():
        raise ValueError(f"not-published package contains reader artifacts: {package_id}")
    return manifest_path, intake_path, evidence_path


def _validate_new_ledgers(
    package_root: Path,
    package_id: str,
    artifact: ResearchArtifactManifest,
    expected_unit_ids: set[str] | None,
) -> tuple[Path, Path, set[str]]:
    reviewed = artifact.reviewed_unit_ids
    reviewed_set = set(reviewed)
    if len(reviewed) != len(reviewed_set):
        raise ValueError(f"duplicate reviewed unit ids: {package_id}")
    if expected_unit_ids is not None and reviewed_set != expected_unit_ids:
        raise ValueError(f"research manifest unit coverage mismatch: {package_id}")
    intake_path = package_root / "intake.jsonl"
    intake = [
        ResearchIntakeEntry.model_validate(row)
        for row in _read_regular_jsonl(intake_path)
    ]
    intake_ids = [value.unit_id for value in intake]
    if len(intake_ids) != len(set(intake_ids)) or set(intake_ids) != reviewed_set:
        raise ValueError(f"research intake unit coverage mismatch: {package_id}")
    evidence_path = package_root / "evidence.jsonl"
    evidence = [
        ResearchEvidenceEntry.model_validate(row)
        for row in _read_regular_jsonl(evidence_path)
    ]
    if not evidence:
        raise ValueError(f"research evidence ledger is empty: {package_id}")
    if any(not set(value.related_unit_ids) <= reviewed_set for value in evidence):
        raise ValueError(f"research evidence contains unknown units: {package_id}")
    return intake_path, evidence_path, reviewed_set


def _normalize_legacy_subreport(value: SubreportArtifact | str) -> SubreportArtifact:
    if isinstance(value, str):
        return SubreportArtifact(slug=Path(value).stem, path=value, unit_ids=[])
    return value


def _validate_subreports(
    package_root: Path,
    subreports: tuple[SubreportArtifact, ...],
    allowed_unit_ids: set[str] | None,
) -> None:
    declared: set[str] = set()
    for value in subreports:
        relative = value.path
        if relative != f"subreports/{value.slug}.md" or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,79}", value.slug
        ):
            raise ValueError(f"unsafe subreport path: {relative}")
        if relative in declared:
            raise ValueError(f"duplicate subreport path: {relative}")
        declared.add(relative)
        if allowed_unit_ids is not None and not set(value.unit_ids) <= allowed_unit_ids:
            raise ValueError(f"subreport contains unknown unit ids: {relative}")
        _read_regular_text(package_root / relative)
    subreport_root = package_root / "subreports"
    actual = (
        {
            str(path.relative_to(package_root))
            for path in subreport_root.glob("*.md")
            if path.is_file() and not path.is_symlink()
        }
        if subreport_root.is_dir()
        else set()
    )
    if actual != declared:
        raise ValueError("declared subreports do not match package files")


def _read_regular_jsonl(path: Path) -> list[dict[str, object]]:
    return parse_jsonl_text(_read_regular_text(path))


def _read_regular_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"research artifact is missing or unsafe: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"research artifact is empty: {path}")
    return content
