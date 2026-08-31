# AI Intelligence Radar

Source-first daily intelligence monitoring for AI, agents, robotics and Physical AI. The system
collects independent source observations, seals a reproducible Phase 1 handoff, lets one serial
Codex thread annotate and package the day, runs package research leads, and publishes a private
Lark Wiki with readable dossiers and natural subreports.

```text
independent sources -> sealed observations -> serial annotate/package -> dossiers -> Brief -> Lark
```

The V1 Claude/Notion/OpenClaw implementation is preserved at the annotated Git tag `legacy-v1`.

## Design boundaries

- Phase 1 collects and performs source-local deduplication. It does not score quality, merge
  platforms, build a semantic graph, or execute third-party code.
- Phase 2 mechanically forms cross-source observation units, then one logical Codex thread processes
  bounded checkpoints and creates 0-15 dynamic research packages. It does not browse or research.
- Phase 3 gives every package to a research lead. Three run concurrently; each may use up to four
  non-recursive subagents and writes a Chinese dossier plus natural subreports.
- Phase 4 creates a Chinese reading/navigation brief without adding research or a forced thesis.
- Phase 5 is deterministic code using the open-source Lark CLI.

See [architecture.md](docs/architecture.md) for the data contracts and [setup.md](docs/setup.md) for
the one-time X, Lark, Codex runner and launchd setup.

## Sources

- Three public X Lists through TwitterAPI.io, with per-List incremental cursors and cross-List Post
  deduplication. A separate cookie-backed Playwright collector captures the personal For You feed
  as a best-effort, non-blocking source.
- GitHub Trending plus bounded recent/activity search lanes for 1–499-star and 500–5000-star
  repos, immutable forward snapshots, 6h/24h/7d deltas, threshold crossings, releases and material
  metadata events.
- arXiv categories `cs.RO`, `cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, `stat.ML`.
- Hugging Face Daily Papers.
- First-party lab/company feeds and sites.
- a16z, The Robot Report and IEEE Spectrum Robotics.
- Hacker News incremental item scanning with new/top/show attention surfaces.

## Local setup

```bash
uv sync --extra dev
npm ci --ignore-scripts
uv run playwright install chromium
cp config/runtime.example.toml config/runtime.toml
cp config/sources.example.toml config/sources.toml
cp config/interests.example.md config/interests.md
uv run ai-digest doctor
uv run ai-digest x-provider-set-key
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
uv run ai-digest tick --event incremental
uv run ai-digest tick --event papers
uv run ai-digest tick --event recover
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

External posts, README text and webpages are untrusted evidence, never instructions. Phase 2–4 run
under the current macOS user with a custom Codex permission profile: only the current run workspace
is writable, while the repository, Codex credential directory, SSH directory and login Keychains
are denied. The queue lives under `~/Library/Application Support/ai-digest/queue`; no second macOS
account or copied Codex login is required. Arbitrary external metadata requests reject private,
loopback, link-local and cloud metadata addresses.

The List API key and browser cookies stay in macOS Keychain or ignored local files. For You browser
automation is explicitly risk-acknowledged, non-required, rate-limited and cooled down after
repeated failures. Lists never receive the personal X cookie.

## License

MIT.
