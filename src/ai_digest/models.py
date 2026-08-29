from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    time_basis: TimeBasis = TimeBasis.OBSERVED
    content_status: ContentStatus = ContentStatus.FULL
    raw_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


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


class CollectorResult(BaseModel):
    source: str
    health: SourceHealth
    items: list[SourceItem] = Field(default_factory=list)
    manifests: list[FetchManifest] = Field(default_factory=list)


class Bundle(BaseModel):
    bundle_id: str
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
    nodes: dict[str, PublishNode] = Field(default_factory=dict)
    dm_idempotency_key: str | None = None
    dm_sent: bool = False
    errors: list[str] = Field(default_factory=list)
