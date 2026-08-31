# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic
versioning for public releases.

## [Unreleased]

## [0.4.0] — 2026-08-31

### Added

- Open-source documentation index, operations/source/verification/Wiki guides, contribution policy,
  issue templates, and CI configuration.
- Daily single-thread semantic grouping with exact unit-to-package catalog coverage.
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
