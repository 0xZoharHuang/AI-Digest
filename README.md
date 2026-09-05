# AI Intelligence Radar

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/production-macOS-000000.svg)](docs/setup.md)

A local-first daily research pipeline for frontier AI, agents, robotics, and Physical AI.

AI Intelligence Radar watches independent source surfaces, preserves replayable observations, asks
Codex agents to investigate the day's new signals, and publishes a private, navigable Lark
Wiki. It is designed for people who want to stay sensitive to a field without manually checking six
different feeds every day.

```text
X / GitHub / papers / media / HN
                │
                ▼
       durable observations
                │
                ▼
 bounded semantic Readers + one daily Editor
                │
                ▼
       parallel research leads
                │
                ▼
 Chinese main reports + optional subreports + daily Brief
                │
                ▼
      private Lark Wiki + self-DM
```

> **Project status:** v0.4 is production-used on macOS under one local user account. The data model,
> queue recovery, no-publish E2E smoke, and Lark publisher are tested. Source coverage remains
> platform-specific: “complete increment”, “bounded discovery”, and “sampled surface” are not treated
> as interchangeable promises.

## Why this project exists

Most digests optimize for summarization. This project optimizes for **durable observation and
traceable research**:

- A source adapter records what the platform actually exposed, not what an editorial layer guessed
  was important.
- A daily run drains every ready, undelivered observation; late arrivals survive outages.
- Phase 2 gives every normalized unit a bounded Codex review, retains all semantic Research objects,
  and consolidates same-object evidence without seeing Phase 3 capacity.
- A separate semantic admission step currently selects at most 15 Research objects for three
  concurrent Phase 3 leads; changing model economics changes this limit, not Phase 2 classification.
- Phase 3 uses each new signal as a starting point for primary-evidence deep research.
- A Lead may withhold a fully inspected package instead of publishing a filler page.
- Phase 4 is a reading map. It does not compress the day into a forced grand narrative.
- Phase 5 is deterministic code: validate files, update the Wiki tree, then send one idempotent DM.

## What gets published

```text
AI Intelligence Radar
└── 2026                         # navigable year index
    └── 2026-08                  # navigable month index
        └── 2026-08-31 · Brief   # daily reading entry point
            ├── Main report A
            │   ├── Subreport A1
            │   └── Subreport A2
            └── Main report B
```

Year and month nodes are real index pages, not empty containers. The publisher adds deterministic
breadcrumbs, verifies every main-report/subreport link before the first external write, and only removes
stale content nodes that are recorded in the same run's publish manifest.

## Pipeline

| Phase | First-principles job | Main artifacts |
|---|---|---|
| 1 — Observe | Save platform-native increments, revisions, receipts, and health without editorial ranking | source JSONL, raw blobs, fetch manifests, `index.json` |
| 2 — Route | Mechanically form exact units; high-recall and precision bounded Codex passes judge every retained full record, then one Editor selects/merges today's concrete research objects | `units.jsonl`, `decisions.jsonl`, `objects.json`, `phase2_manifest.json` |
| 3 — Research | Semantically admit a configured number from all Research objects, then run one lead per selected object | `phase3_admission.json`, `main_report.md`, `intake.jsonl`, `evidence.jsonl`, optional `subreports/*.md` |
| 4 — Navigate | Build a reader-facing Brief that links to research and exposes failures/unknowns | `daily_brief.md`, quality and source-health files |
| 5 — Publish | Validate and update Lark content idempotently; independently notify success, partial, retrying, or failure state | `publish_manifest.json`, notification receipts |

The detailed contracts are in [Architecture and data contracts](docs/architecture.md).

## Sources and coverage semantics

| Source | Access path | Coverage mode | Offline recovery |
|---|---|---|---|
| Public X Lists | TwitterAPI.io List timeline | complete configured-list increment | `sinceTime` cursor + overlap |
| X For You | opted-in Playwright session | sampled surface | cannot reconstruct missed recommendations |
| GitHub | Trending + bounded Search + tracked repos | bounded discovery | multi-day queries + immutable snapshots |
| arXiv | official multi-category RSS | complete current announcement feed while online | submitted-date API is a bounded supplement, not announcement replay |
| Hugging Face Papers | official dated API | bounded attention surface | dated page backlog |
| Hacker News | official Firebase IDs | complete incremental ID scan | bounded `maxitem` chunks |
| Media | RSS/Atom, sitemap, then index adapters | bounded publisher surface | conditional requests + retained feed/index history |

Configured media include OpenAI, NVIDIA, Hugging Face, Physical Intelligence, Anthropic, DeepMind,
Figure, 1X, Skild, a16z, IEEE Spectrum Robotics, and The Robot Report. See
[Source contracts](docs/sources.md) for exact cursor, revision, and limitation semantics.

## Requirements

- macOS for the production LaunchAgent and Keychain path
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20+ and npm
- GitHub CLI (`gh`) for authenticated GitHub collection
- a current ChatGPT/Codex CLI login
- a Lark Wiki plus user/bot authorization for publishing
- optional: TwitterAPI.io credits and an explicitly opted-in personal X browser session

Linux can run much of the library and test suite, but the production scheduler, Keychain storage,
Playwright login handoff, and acceptance evidence are currently macOS-first. Windows support is not
part of v0.4.

## Quick start

```bash
git clone https://github.com/0xZoharHuang/AI-Digest.git
cd AI-Digest

uv sync --extra dev
npm ci --ignore-scripts
uv run playwright install chromium

cp config/runtime.example.toml config/runtime.toml
cp config/sources.example.toml config/sources.toml
cp config/interests.example.md config/interests.md

uv run ai-digest doctor
```

Configure only the sources you intend to use. Local configuration, browser cookies, runtime data,
logs, and credentials are ignored by Git.

Useful first runs:

```bash
# Read-only source checks
uv run ai-digest collect --source arxiv --source huggingface

# Local pipeline; does not publish
uv run ai-digest pipeline

# Isolated owner runtime + real Codex worker + publish preflight; never calls Lark
# This verifies integration, not Phase 2 semantic quality.
uv run ai-digest automation-smoke
```

For X List and browser setup, Lark authorization, and current-user installation, follow
[Setup and rollout](docs/setup.md).

## Production on macOS

The installer is intentionally two-step:

```bash
./scripts/install_macos.sh          # dry-run only
./scripts/install_macos.sh --apply  # immutable install + pending plists; loaded schedule unchanged

uv run ai-digest doctor
uv run ai-digest automation-smoke

./scripts/install_macos.sh --cutover

# Switch back to the recorded previous immutable V3 snapshot.
./scripts/install_macos.sh --rollback-v3
```

Cutover loads three current-user LaunchAgents:

- `com.ai-digest.tick`: calendar collection plus login/wake catch-up;
- `com.ai-digest.agent-runner`: watches `jobs/` and runs Phases 2–4;
- `com.ai-digest.recover`: watches `completed/`, runs every 15 minutes, promotes due agent retries,
  retries Lark publication, and starts a missing daily run after 07:00.

No second macOS account is required. The worker reuses the current user's file-backed Codex login
without copying it into a job workspace.

## Failure and recovery model

```text
sealed Phase 1
    │
    ▼
jobs ── Codex transient failure ──► retry_wait ── due ──► jobs
    │
    └── success / terminal result ─► completed
                                      │
                                      ├── Lark failure ─► publish_pending
                                      │                     │
                                      │                     └── due retry
                                      └── success ────────► archived
```

- Queue moves are atomic directory renames.
- Source cursors advance only after their durable batch is committed.
- Phase 2 batch and Phase 3 package checkpoints survive process interruption.
- Codex non-zero exits receive bounded retry without classifying Agent prose.
- Lark retries preserve the same artifact hash and DM idempotency key.
- `tick`, worker, and recovery each use a non-blocking process lock.
- `RunAtLoad` repairs missed laptop schedules after login/wake.
- Before cutover, the installer records a currently loaded immutable V3 snapshot when one exists.
  Retention protects that reverse target when present, then keeps the newest snapshots until the
  active/protected set reaches three builds. On queue races, failed bootstraps, wrong loaded paths, and non-zero exits, cutover
  attempts to restore the safely loaded V3 and reports the outcome before pruning. Once the new labels
  are healthy, pruning failure is only a warning and the new V3 remains active. `--rollback-v3`
  switches between the active and recorded previous V3
  snapshots without enabling the legacy schedule. Automatic V1 rollback is unsupported; follow the
  [legacy-v1 tag](https://github.com/0xZoharHuang/AI-Digest/tree/legacy-v1) to stop or migrate it before cutover.
- Apply, cutover, and V3 rollback share one non-blocking runtime installer lock. Apply repairs
  on-disk plists to the safely loaded V3 for login continuity, invalidates stale pending targets, and
  stages the new credential-free plists under the runtime. Only cutover installs those pending plists
  into `~/Library/LaunchAgents`; failed cutover keeps them for a retry.

The operational runbook is in [Operations and recovery](docs/operations.md).

## Commands

```bash
uv run ai-digest doctor
uv run ai-digest collect [--source NAME] [--verbose]
uv run ai-digest phase1
uv run ai-digest route RUN_DIR
uv run ai-digest research RUN_DIR
uv run ai-digest brief RUN_DIR
uv run ai-digest publish RUN_DIR
uv run ai-digest pipeline [--publish]
uv run ai-digest tick --event {daily,incremental,papers,x-list,x-for-you,github,recover}
uv run ai-digest agent-worker
uv run ai-digest automation-smoke [--stage full|prepare|verify]
uv run ai-digest maintenance --prune-x
```

See [CLI and configuration](docs/configuration.md) for command behavior and configuration fields.

## Security and privacy

- External posts, pages, repositories, and papers are untrusted evidence, never instructions.
- Agent calls use explicit read-only/workspace-write permission profiles.
- Codex home, SSH files, login Keychains, repository source, and unrelated user files are denied to
  research workspaces.
- TwitterAPI.io keys stay in Keychain; personal X cookies stay in an ignored file.
- Arbitrary metadata fetches reject private, loopback, link-local, and cloud-metadata destinations.
- Raw source text is local by default; publishing targets a private Wiki.

Please read [SECURITY.md](SECURITY.md) before running personal browser collection or reporting a
vulnerability.

## Verification

```bash
uv run ruff check src/ai_digest tests scripts
uv run mypy src/ai_digest
uv run pytest --cov=ai_digest
npm ci --ignore-scripts --dry-run
```

The coverage gate is 85% for the core state/Phase 1 modules. Live source checks, installed-snapshot
doctor, a real no-publish automation smoke, and LaunchAgent queue traversal are separate evidence
levels; a unit test alone is not production sign-off. See [Verification guide](docs/verification.md).

## Documentation

- [Documentation index](docs/README.md)
- [Architecture and data contracts](docs/architecture.md)
- [Source contracts](docs/sources.md)
- [CLI and configuration](docs/configuration.md)
- [Setup and rollout](docs/setup.md)
- [Operations and recovery](docs/operations.md)
- [Verification guide](docs/verification.md)
- [Wiki information architecture](docs/wiki.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Legacy implementation

The earlier Claude/Notion/OpenClaw implementation is preserved at the annotated Git tag
`legacy-v1`. v0.4 does not keep both architectures alive in the same production tree.

## License

[MIT](LICENSE) © 2026 Zohar Huang.
