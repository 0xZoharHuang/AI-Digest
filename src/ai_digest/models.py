from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class HealthStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    DISABLED = "disabled"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    QUIET = "quiet"
    FAILED = "failed"
    SKIPPED_ASLEEP = "skipped_asleep"


class TimeBasis(StrEnum):
    OCCURRED = "occurred"
    UPDATED = "updated"
    OBSERVED = "observed"


class ContentStatus(StrEnum):
    FULL = "full"
    PREVIEW = "preview"
    METADATA_ONLY = "metadata_only"
    EXTRACTION_FAILED = "extraction_failed"
    TOMBSTONE = "tombstone"


class ObservationKind(StrEnum):
    LIVE_INCREMENT = "live_increment"
    LATE_ARRIVAL = "late_arrival"
    CONTENT_REVISION = "content_revision"
    BOOTSTRAP_SNAPSHOT = "bootstrap_snapshot"


class CoverageMode(StrEnum):
    COMPLETE_INCREMENT = "complete_increment"
    BOUNDED_DISCOVERY = "bounded_discovery"
    SAMPLED_SURFACE = "sampled_surface"


class SourceItem(BaseModel):
    schema_version: int = 1
    item_id: str
    item_type: str
    source: str
    surface: str
    change: str = "first_seen"
    occurred_at: datetime | None = None
    updated_at: datetime | None = None
    first_observed_at: datetime = Field(default_factory=utc_now)
    handoff_at: datetime = Field(default_factory=utc_now)
    ready_at: datetime = Field(default=None)  # type: ignore[assignment]
    observation_kind: ObservationKind = ObservationKind.LIVE_INCREMENT
    entity_key: str | None = None
    content_hash: str | None = None
    time_basis: TimeBasis = TimeBasis.OBSERVED
    content_status: ContentStatus = ContentStatus.FULL
    raw_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.ready_at is None:
            self.ready_at = self.first_observed_at


class FetchManifest(BaseModel):
    schema_version: int = 1
    fetch_id: str
    source: str
    started_at: datetime
    completed_at: datetime
    request: dict[str, Any]
    response_status: int | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)
    blob_refs: list[str] = Field(default_factory=list)
    cursor_before: str | None = None
    cursor_after: str | None = None
    fetched_count: int = 0
    parsed_count: int = 0
    status: HealthStatus = HealthStatus.SUCCESS
    errors: list[str] = Field(default_factory=list)


class SourceHealth(BaseModel):
    source: str
    status: HealthStatus
    fetched_count: int = 0
    parsed_count: int = 0
    new_count: int = 0
    duration_seconds: float = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    surfaces: dict[str, dict[str, Any]] = Field(default_factory=dict)
    coverage_mode: CoverageMode = CoverageMode.COMPLETE_INCREMENT
    duplicate_count: int = 0
    revision_count: int = 0
    oldest_occurred_at: datetime | None = None
    newest_occurred_at: datetime | None = None
    raw_receipts_complete: bool = True
    quiet_reason: str | None = None


class ObservationUnit(BaseModel):
    unit_id: str
    entity_key: str
    item_ids: list[str]
    sources: list[str]
    occurred_at: datetime | None = None
    summary: str = ""
    projection: dict[str, Any] = Field(default_factory=dict)


class Phase2UnitDocument(BaseModel):
    """Lossless Phase 2 view over normalized Phase 1 observations."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    entity_key: str
    item_ids: list[str]
    sources: list[str]
    occurred_at: datetime | None = None
    observations: list[SourceItem] = Field(min_length=1)


class Phase2Decision(BaseModel):
    """Read-only compatibility model for attention_editor_v1."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    route: Literal["research", "watch", "archive"]
    cluster_hint: str = ""
    trigger_zh: str = ""

    @field_validator("cluster_hint", "trigger_zh")
    @classmethod
    def decision_text_is_trimmed(cls, value: str) -> str:
        return value.strip()

    def model_post_init(self, __context: Any) -> None:
        if self.route in {"research", "watch"} and (not self.cluster_hint or not self.trigger_zh):
            raise ValueError("research/watch decisions require cluster_hint and trigger_zh")


class Phase2RoutingDecision(BaseModel):
    """Minimal decision contract produced by attention_editor_v2."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    route: Literal["research", "watch", "archive"]
    object_id: str = ""
    reason_zh: str = ""

    @field_validator("object_id", "reason_zh")
    @classmethod
    def routing_text_is_trimmed(cls, value: str) -> str:
        return value.strip()

    def model_post_init(self, __context: Any) -> None:
        if self.route == "research" and (not self.object_id or not self.reason_zh):
            raise ValueError("research decisions require object_id and reason_zh")
        if self.route == "watch" and not self.reason_zh:
            raise ValueError("watch decisions require reason_zh")
        if self.route == "archive" and (self.object_id or self.reason_zh):
            raise ValueError("archive decisions must not include editorial text")


class Phase2ProvisionalDecision(BaseModel):
    """Bounded-review judgment before cross-batch object consolidation."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    route: Literal["research", "watch", "archive"]
    object_key: str = ""
    object_label_zh: str = ""
    reason_zh: str = ""

    @field_validator("object_key", "object_label_zh", "reason_zh")
    @classmethod
    def provisional_text_is_trimmed(cls, value: str) -> str:
        return value.strip()

    def model_post_init(self, __context: Any) -> None:
        if self.route == "research" and (
            not self.object_key or not self.object_label_zh or not self.reason_zh
        ):
            raise ValueError("provisional research decisions require object identity and reason")
        if self.route == "watch" and not self.reason_zh:
            raise ValueError("provisional watch decisions require a reason")
        if bool(self.object_key) != bool(self.object_label_zh):
            raise ValueError("provisional object key and label must appear together")
        if self.route == "archive" and (self.object_key or self.object_label_zh or self.reason_zh):
            raise ValueError("provisional archive decisions must not include editorial text")


class Phase2ResearchObject(BaseModel):
    """Same-object grouping only; Phase 3 owns the research scope."""

    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label_zh: str
    unit_ids: list[str] = Field(min_length=1)

    @field_validator("label_zh")
    @classmethod
    def object_label_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("object label must not be blank")
        return value.strip()


class Phase2WatchSignal(BaseModel):
    """Read-only compatibility model for attention_editor_v1."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title_zh: str
    note_zh: str
    unit_ids: list[str] = Field(min_length=1)

    @field_validator("title_zh", "note_zh")
    @classmethod
    def watch_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("watch signal text must not be blank")
        return value.strip()


class Phase2Annotation(BaseModel):
    """Read-only compatibility model for V3 routing artifacts."""

    unit_id: str
    disposition: Literal["investigate", "supporting", "duplicate", "discard"]
    summary_zh: str
    reason: str
    entities: list[str] = Field(default_factory=list)
    relation_hints: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None


class Phase2Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    summary_zh: str
    group_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")

    @field_validator("summary_zh")
    @classmethod
    def summary_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary_zh must not be blank")
        return value.strip()


class Phase2CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    summary_zh: str
    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")

    @field_validator("summary_zh")
    @classmethod
    def summary_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary_zh must not be blank")
        return value.strip()


class LegacyResearchPackage(BaseModel):
    """Read-only compatibility model for V3 package files."""

    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str
    investigate_unit_ids: list[str]
    supporting_unit_ids: list[str] = Field(default_factory=list)


class ResearchPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label_zh: str
    scope_note_zh: str
    unit_ids: list[str] = Field(min_length=1)

    @field_validator("label_zh", "scope_note_zh")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("package text must not be blank")
        return value.strip()


class Phase3Admission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    daily_agent_limit: int = Field(ge=0)
    concurrency: int = Field(ge=1)
    selection_mode: Literal["all", "codex_priority", "disabled"]
    selector_model: str = ""
    selector_reasoning: str = ""
    thread_id: str | None = None
    available_object_ids: list[str]
    selected_object_ids: list[str]
    not_scheduled_object_ids: list[str]

    def model_post_init(self, __context: Any) -> None:
        available = self.available_object_ids
        selected = self.selected_object_ids
        not_scheduled = self.not_scheduled_object_ids
        if (
            len(available) != len(set(available))
            or len(selected) != len(set(selected))
            or len(not_scheduled) != len(set(not_scheduled))
        ):
            raise ValueError("Phase 3 admission contains duplicate object ids")
        if len(selected) > min(self.daily_agent_limit, len(available)) or not set(selected) <= set(
            available
        ):
            raise ValueError("Phase 3 admission selected set is invalid")
        if not_scheduled != [value for value in available if value not in set(selected)]:
            raise ValueError("Phase 3 admission does not preserve unscheduled objects")
        if self.selection_mode == "all" and selected != available:
            raise ValueError("all-mode admission must select every object")
        if self.selection_mode == "disabled" and (self.daily_agent_limit != 0 or selected):
            raise ValueError("disabled admission requires zero budget and no selection")
        if self.selection_mode == "codex_priority" and (
            not self.selector_model or not self.selector_reasoning or not self.thread_id
        ):
            raise ValueError("Codex admission requires selector metadata")


class Phase2PackagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label_zh: str
    scope_note_zh: str
    group_ids: list[str] = Field(min_length=1)

    @field_validator("label_zh", "scope_note_zh")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("package plan text must not be blank")
        return value.strip()


class SubreportArtifact(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    path: str
    unit_ids: list[str] = Field(default_factory=list)


class LegacyResearchArtifactManifest(BaseModel):
    """Read-only compatibility model for V3 dossier artifacts."""

    package_id: str
    dossier: str
    subreports: list[SubreportArtifact | str] = Field(default_factory=list)
    primary_unit_ids: list[str] = Field(default_factory=list)
    unresolved_unit_ids: list[str] = Field(default_factory=list)
    missing_unit_ids: list[str] = Field(default_factory=list)
    status: str = "success"


class ResearchIntakeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    research_use: Literal["research_subject", "evidence", "context", "not_used"]
    note_zh: str

    @field_validator("note_zh")
    @classmethod
    def note_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note_zh must not be blank")
        return value.strip()


class ResearchEvidenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    status: Literal[
        "verified_fact",
        "source_claim",
        "inference",
        "disputed",
        "unknown",
    ]
    evidence: list[str] = Field(default_factory=list)
    scope: str = ""
    conflict: str = ""
    related_unit_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def one_evidence_locator_becomes_a_list(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return value

    @field_validator("scope", "conflict", mode="before")
    @classmethod
    def optional_evidence_text_accepts_null(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("claim")
    @classmethod
    def claim_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim must not be blank")
        return value.strip()


class ResearchArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str
    main_report: Literal["main_report.md"] | None
    subreports: list[SubreportArtifact] = Field(default_factory=list)
    reviewed_unit_ids: list[str] = Field(default_factory=list)
    status: Literal["success", "complete", "not_published"] = "success"


class CollectorResult(BaseModel):
    source: str
    health: SourceHealth
    items: list[SourceItem] = Field(default_factory=list)
    manifests: list[FetchManifest] = Field(default_factory=list)


class Bundle(BaseModel):
    bundle_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str
    item_ids: list[str]


class Assignment(BaseModel):
    id: str
    d: Literal["r", "w", "n"]
    t: list[str] = Field(default_factory=list)


class RoutingOutput(BaseModel):
    bundles: list[Bundle]
    assignments: list[Assignment]
    quiet_reason: str | None = None


class AgentAttempt(BaseModel):
    phase: str
    object_id: str
    attempt: int
    model: str
    reasoning: str
    started_at: datetime
    completed_at: datetime | None = None
    thread_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "running"
    error_class: str | None = None
    error: str | None = None


class RunManifest(BaseModel):
    schema_version: int = 1
    run_id: str
    date: str
    attempt: int
    timezone: str
    window_start: datetime
    window_end: datetime
    status: RunStatus = RunStatus.PENDING
    phases: dict[str, RunStatus] = Field(default_factory=dict)
    source_health: dict[str, SourceHealth] = Field(default_factory=dict)
    context_hashes: dict[str, str] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    agent_attempts: list[AgentAttempt] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PublishNode(BaseModel):
    key: str
    title: str
    node_token: str
    obj_token: str
    url: str | None = None
    content_hash: str | None = None
    status: str = "created"


class PublishManifest(BaseModel):
    run_id: str
    status: str = "pending"
    navigation_version: int = 0
    artifact_hash: str | None = None
    nodes: dict[str, PublishNode] = Field(default_factory=dict)
    dm_idempotency_key: str | None = None
    dm_sent: bool = False
    dm_identity: str | None = None
    dm_message_id: str | None = None
    dm_chat_id: str | None = None
    errors: list[str] = Field(default_factory=list)
