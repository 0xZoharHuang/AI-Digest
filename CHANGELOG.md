# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic
versioning for public releases.

## [Unreleased]

### Changed

- Decouple Phase 2 semantic Research/Watch/Archive decisions from Phase 3 execution capacity. Phase 2
  retains every Research object; a separate semantic admission step currently selects at most 15
  top-level Phase 3 leads without relabeling the remainder.
- Require two independent full-record Reader decisions before Phase 2 may Archive a unit; any Reader
  disagreement remains available to the final Editor, addressing systemic Archive false negatives.
- Make the Feishu self-DM an independent operational channel for terminal, partial, and retrying
  states, with persistent idempotent receipts and recovery retries even when Wiki content cannot be
  published.
- Replace the one-context/source-lane Phase 2 path with a high-recall bounded full-record pass, a
  smaller precision pass over retained units, and one higher-capability Editor that owns the
  daily Phase 3 queue, Archive audit and concrete object consolidation.
- Keep the production handoff minimal as `decisions.jsonl` plus `objects.json`; Watch evidence may
  attach to a Research object without being promoted to Research, while Phase 3 retains research
  scope and report design.

### Fixed

- Route attention-editor objects through the formal Phase 3 artifact contract instead of the legacy
  single `report.md` path; carry Watch decisions into Phase 4, record independent Phase 4 quality,
  and reject incomplete Phase 3/4 evidence before the first Lark write.
- Include research quality, `not_published` decisions, and Phase 4 quality in the publish artifact
  hash, and retain an explicit receipt for isolated Phase 5 preflight runs.
- Reject duplicate, missing, or unknown unit IDs even when a batch response satisfies JSON Schema,
  repair only that batch in its saved Codex session, and retain valid batches across interruption.
- Preserve the currently loaded immutable V3 snapshot during cutover, attempt and report its recovery
  when a replacement LaunchAgent bootstrap fails, and support an explicit reversible
  `--rollback-v3` switch without falling through to the legacy V1 schedule.
- Keep new LaunchAgent plists pending until direct health checks pass, isolate stale on-disk V3 plists
  during apply, serialize installer mutations, and reject legacy V1 schedules for manual migration
  instead of attempting an automatic V1 rollback.
- Continue a partial Phase 2 repair part with bounded, missing-ID-only completion turns instead of
  terminating a large daily run after the first incomplete repair response.
- Make new Phase 2 batches an exact required-key assignment map (`unit_id` to `group_id`) while
  deriving internal navigation previews mechanically from Phase 1 and preserving legacy summary and
  list-assignment checkpoints on resume.
- Normalize explicit `null` values in optional Phase 3 evidence scope/conflict fields so otherwise
  complete research artifacts are validated and published instead of downgraded to a package failure.

## [0.4.0] — 2026-08-31

### Added

- Open-source documentation index, operations/source/verification/Wiki guides, contribution policy,
  issue templates, and CI configuration.
- Daily single-thread semantic grouping with continuous group IDs and exact unit-to-package catalog
  coverage.
- Artifact-level Phase 3 deep research with `main_report.md`, intake/evidence ledgers, and optional
  subreports, using GPT-5.6 Sol at medium reasoning.

### Changed

- Phase 2 no longer makes importance or research-depth decisions; every unit reaches exactly one
  Phase 3 package.
- Wiki research pages are self-contained main reports rather than dossier navigation wrappers.

## [0.3.0] — 2026-08-31

### Added

- Observation-led Phase 1 with ready-time delivery, revisions, fetch receipts, source health, and
  platform-specific cursor semantics.
- V3 serial annotation and dynamic research packaging.
- Independent package research leads producing Simplified-Chinese dossiers and natural subreports.
- Reader-navigation Brief and hierarchical private Lark Wiki with idempotent self-DM.
- Isolated real-Codex automation smoke and in-memory Wiki publisher verification.
- Current-user immutable macOS installation with tick, worker, and recovery LaunchAgents.
- Wake/login catch-up, Codex retry queue, Lark publish backoff, process locks, and app-snapshot
  retention.

### Changed

- Replaced the legacy Claude/Notion/OpenClaw production path; that implementation remains available at
  tag `legacy-v1`.

### Security

- Added explicit Codex filesystem permission profiles and denied Codex home, SSH, login Keychains,
  repository source, and unrelated user files from Agent workspaces.
- Kept X List API keys in Keychain and personal X browser cookies in ignored local configuration.
