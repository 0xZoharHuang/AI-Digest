# AI Intelligence Radar

Source-first daily intelligence monitoring for AI, agents, robotics and Physical AI. The system
collects independent source records, seals a reproducible Phase 1 handoff, lets Codex route and
research provisional bundles, writes one human-readable brief, and publishes a private Lark Wiki.

```text
independent sources -> sealed typed JSONL -> Router -> Research Agents -> Brief -> Lark
```

The V1 Claude/Notion/OpenClaw implementation is preserved at the annotated Git tag `legacy-v1`.

## Design boundaries

- Phase 1 collects and performs source-local deduplication. It does not score quality, merge
  platforms, build a semantic graph, or execute third-party code.
- Phase 2 is one read-only Codex Router. It assigns every source item to research, watch, or
  no-action and creates at most 18 provisional bundles.
- Phase 3 gives every bundle to a complete Codex research agent. Three run concurrently; each may
  use up to four non-recursive subagents and must leave one `report.md`.
- Phase 4 reads successful reports and watch items and returns one `daily_brief.md`.
- Phase 5 is deterministic code using the open-source Lark CLI.

See [architecture.md](docs/architecture.md) for the data contracts and [setup.md](docs/setup.md) for
the one-time X, Lark, Codex runner and launchd setup.

## Sources

- X private List via the official API; X For You via an explicitly enabled local Playwright profile.
- GitHub Trending plus two bounded search lanes: recent active 1–499-star and 500–5000-star repos.
- arXiv categories `cs.RO`, `cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, `stat.ML`.
- Hugging Face Daily Papers.
- First-party lab/company feeds and sites.
- a16z, The Robot Report and IEEE Spectrum Robotics.
- Hacker News new/top/show.

## Local setup

```bash
uv sync --extra dev
npm ci --ignore-scripts
uv run playwright install chromium
cp config/runtime.example.toml config/runtime.toml
cp config/sources.example.toml config/sources.toml
cp config/interests.example.md config/interests.md
uv run ai-digest doctor
uv run ai-digest x-auth
uv run ai-digest x-list-bootstrap
uv run ai-digest x-login
```

Local config, credentials and runtime data are ignored by Git.

## Commands

```bash
uv run ai-digest doctor
uv run ai-digest collect --source arxiv --source huggingface
uv run ai-digest phase1
uv run ai-digest route /path/to/run
uv run ai-digest research /path/to/run
uv run ai-digest brief /path/to/run
uv run ai-digest publish /path/to/run
uv run ai-digest pipeline              # local development, no publish
uv run ai-digest pipeline --publish
uv run ai-digest tick --event daily
uv run ai-digest agent-worker
```

`phase1` writes `PHASE1_COMPLETE` only after all enabled collectors reach `success`, `partial`, or
`failed` and the typed handoff is immutable. Phase 2 refuses to start without this marker.

## Verification

```bash
uv run ruff check src/ai_digest tests
uv run mypy src/ai_digest
uv run pytest --cov=ai_digest
```

The core state and Phase 1 modules are coverage-gated at 85%. Live adapters have fixture tests and
must additionally pass `doctor` and a no-publish smoke run before launchd is installed.

## Security

External posts, README text and webpages are untrusted evidence, never instructions. The production
design runs Phase 2–4 under a dedicated macOS standard user and exchanges only sanitized run files
through `/Users/Shared/ai-digest-runtime`. The main user retains X, GitHub, browser and Lark
credentials. Arbitrary external metadata requests reject private, loopback, link-local and cloud
metadata addresses.

X For You browser automation is disabled in the public example because X's current developer rules
prohibit non-API browser automation. Enabling it is an explicit local risk decision. X API content
is indexed with a 30-day text-retention expiry and requires compliance deletion handling.

## License

MIT.
