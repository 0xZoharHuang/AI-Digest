# Contributing

Thanks for helping improve AI Intelligence Radar. The project values explicit source semantics,
durable state transitions, reader-quality research, and evidence-backed verification over clever but
opaque abstractions.

## Before opening a change

1. Read [README.md](README.md), [Architecture](docs/architecture.md), and the relevant source or
   operations contract.
2. Search existing issues and pull requests.
3. Keep a change within one system boundary when possible: source adapter, state/queue, Agent phase,
   publisher, docs, or deployment.
4. Never include local credentials, cookies, Lark IDs, raw private source content, runtime databases,
   or production logs.

## Development setup

```bash
uv sync --extra dev
npm ci --ignore-scripts

uv run ruff check src/ai_digest tests scripts
uv run mypy src/ai_digest
uv run pytest --cov=ai_digest
```

Run live adapters only against an isolated runtime. Do not publish or alter a real Wiki as part of a
routine test.

## Source-adapter checklist

A new or changed source must document and test:

- platform surface and coverage mode;
- stable object/revision identity;
- source time, first-observed time, and ready time;
- pagination/cursor before and after;
- raw receipt persistence;
- deduplication and revision behavior;
- empty/quiet semantics;
- partial page/request failure and cursor retention;
- offline/backlog behavior;
- bootstrap classification;
- content retention/privacy constraints.

The cursor must advance after durable evidence and state, never before.

## Agent-phase checklist

- Keep external text untrusted.
- Preserve exact unit/package coverage invariants.
- Do not tighten natural-language output with regexes; strict validation belongs to system-owned IDs,
  schemas, relative paths, and internal URI protocols.
- Keep Phase 2 non-browsing, Phase 3 evidence-seeking, and Phase 4 navigational.
- Add checkpoint/recovery tests when changing a long-running phase.
- Report token/concurrency tradeoffs rather than silently shrinking context.

## Pull requests

A pull request should include:

- the user-visible problem and system boundary;
- data/schema/migration impact;
- security/privacy impact;
- commands and exact test results;
- live or installed evidence when the change touches external adapters, launchd, Codex, or Lark;
- rollback notes for stateful/deployment changes.

Do not label a PR production-ready from fixture tests alone.

## Commit style

Use imperative, outcome-oriented subjects, for example:

```text
Retain HN cursor on partial backlog failure
Add publish preflight before Wiki writes
Document sampled X recommendation semantics
```

## Reporting bugs

Use the issue templates for non-sensitive bugs and feature requests. Report vulnerabilities through
the private path in [SECURITY.md](SECURITY.md).
