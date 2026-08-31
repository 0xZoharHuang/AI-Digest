# Verification guide

“Tests pass” and “production is accepted” are different claims.

## Evidence levels

| Level | Evidence | What it proves |
|---|---|---|
| 1 | Ruff, Mypy, unit/fixture tests | local contracts and regressions |
| 2 | live adapter check in isolated runtime | current upstream interface and parser behavior |
| 3 | `doctor` from the immutable installed snapshot | installed binaries, auth, model, browser, Wiki, disk |
| 4 | full `automation-smoke` | real Codex Phase 1–4, queue import, publish preflight, Wiki dry-run |
| 5 | temporary/production LaunchAgent queue traversal | macOS `QueueDirectories` and installed process environment |
| 6 | natural production canary | real schedule, live volume, private Wiki readback, idempotent DM |

Do not collapse these into one “green” statement.

## Core verification

```bash
uv run ruff check src/ai_digest tests scripts
uv run mypy src/ai_digest
uv run pytest --cov=ai_digest
npm ci --ignore-scripts --dry-run
```

The core coverage gate is 85%. Agent orchestration, external CLIs, collectors, publisher, and smoke
driver have contract/fixture tests but are not misrepresented by line coverage alone.

## Automation smoke acceptance

```bash
uv run ai-digest automation-smoke
```

The smoke must prove:

- a real Phase 1 runner writes and seals the fixture;
- the fixture covers X List, For You, GitHub, media, HN, and an arXiv/HF pair;
- literal U+2028/U+2029 and a referenced blob cross the JSONL queue boundary;
- every item belongs to exactly one unit and every unit has exactly one annotation;
- at least one research package exists;
- Phase 3 failure and missing-unit counts are zero;
- `DONE=complete`, not failure fallback;
- queue transition is `jobs -> completed -> archived`;
- publish preflight resolves every internal link;
- the in-memory Wiki has no empty pages and makes zero live Lark calls.

The receipt is written below the chosen smoke root as `automation_smoke_receipt.json`.

## Failure injection

Tests include:

- legal JSON strings containing Unicode line/paragraph separators;
- interrupted enqueue and visible-job recovery;
- partial Phase 2 output without a resumable thread;
- Phase 3 supporting evidence in subreport manifests;
- Codex retry metadata written before the queue move;
- due/not-due agent retry promotion;
- Lark backoff and DM retry without Wiki write amplification;
- HN bounded backlog and failed-chunk cursor retention;
- HF pagination-cap and data-before-cursor durability;
- tick/worker/recovery process locks;
- stale Wiki content-node cleanup scoped to one publish manifest.

## Production canary

For the first natural daily run after a release, verify:

1. source receipts and coverage modes;
2. Phase 2 unit/annotation/package exactness;
3. Phase 3 `failures.json`, `quality.json`, and each research manifest;
4. Brief links and Wiki year/month/day/dossier/subreport hierarchy;
5. Lark `publish_manifest.json`, real message/chat IDs, and no duplicate DM;
6. queue directories empty or in an explained retry state;
7. log modification times after the installed snapshot timestamp.
