from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _repo_root() -> Path:
    explicit = os.environ.get("AI_DIGEST_PROJECT_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "config").is_dir():
        return source_root
    working_root = Path.cwd().resolve()
    if (working_root / "config").is_dir():
        return working_root
    return source_root


REPO_ROOT = _repo_root()


class CodexConfig(BaseModel):
    binary: str = "./node_modules/.bin/codex"
    router_model: str = "gpt-5.6-sol"
    router_reasoning: str = "high"
    router_reader_model: str = "gpt-5.6-terra"
    router_reader_reasoning: str = "high"
    router_reader_concurrency: int = Field(default=4, ge=1, le=16)
    router_decider_model: str = "gpt-5.6-terra"
    router_decider_reasoning: str = "high"
    router_decider_concurrency: int = Field(default=4, ge=1, le=16)
    research_model: str = "gpt-5.6-sol"
    research_reasoning: str = "medium"
    brief_model: str = "gpt-5.6-sol"
    brief_reasoning: str = "high"
    phase3_daily_agent_limit: int = Field(default=15, ge=1, le=1000)
    top_level_concurrency: int = 3
    subagent_threads: int = 4
    idle_timeout_seconds: int = 900


class LarkConfig(BaseModel):
    binary: str = "./node_modules/.bin/lark-cli"
    space_id: str = ""
    receiver_open_id: str = ""
    wiki_name: str = "AI Intelligence Radar"
    wiki_base_url: str = "https://feishu.cn/wiki"
    identity: str = "user"
    dm_identity: str = "bot"


class RuntimeConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    runtime_root: Path = Path("~/Library/Application Support/ai-digest")
    shared_runtime_root: Path = Path("~/Library/Application Support/ai-digest/queue")
    daily_hour: int = 7
    window_hours: int = 24
    article_preview_chars: int = 4000
    x_text_retention_days: int = 30
    codex: CodexConfig = Field(default_factory=CodexConfig)
    lark: LarkConfig = Field(default_factory=LarkConfig)

    def model_post_init(self, __context: Any) -> None:
        self.runtime_root = self.runtime_root.expanduser()
        self.shared_runtime_root = self.shared_runtime_root.expanduser()


class SourcesConfig(BaseModel):
    x_list: dict[str, Any] = Field(default_factory=dict)
    x_for_you: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] = Field(default_factory=dict)
    arxiv: dict[str, Any] = Field(default_factory=dict)
    huggingface: dict[str, Any] = Field(default_factory=dict)
    hackernews: dict[str, Any] = Field(default_factory=dict)
    articles: list[dict[str, Any]] = Field(default_factory=list)


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def resolve_config_path(name: str, explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    local = REPO_ROOT / "config" / f"{name}.toml"
    if local.exists():
        return local
    return REPO_ROOT / "config" / f"{name}.example.toml"


def load_runtime_config(path: str | Path | None = None) -> RuntimeConfig:
    config = RuntimeConfig.model_validate(_read_toml(resolve_config_path("runtime", path)))
    override = os.environ.get("AI_DIGEST_RUNTIME_ROOT")
    if override:
        config.runtime_root = Path(override).expanduser().resolve()
    return config


def load_sources_config(path: str | Path | None = None) -> SourcesConfig:
    return SourcesConfig.model_validate(_read_toml(resolve_config_path("sources", path)))


def load_interests(path: str | Path | None = None) -> str:
    if path:
        candidate = Path(path).expanduser()
    else:
        local = REPO_ROOT / "config" / "interests.md"
        candidate = local if local.exists() else REPO_ROOT / "config" / "interests.example.md"
    return candidate.read_text(encoding="utf-8")


def resolve_binary(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((REPO_ROOT / path).resolve())
