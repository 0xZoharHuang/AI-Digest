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

## Phase 2 semantic acceptance

`automation-smoke` is not a semantic acceptance test for Phase 2. Its small synthetic fixture proves
that the current Phase 2 contract can cross the queue boundary and drive Phase 3–5; it cannot measure
daily recall, source bias, Research/Watch calibration, or object identity quality.

Before changing the production Phase 2 model, prompt, batching policy, or object contract, run the
candidate against an immutable full-day corpus and keep development-only evidence outside the live
runtime:

```bash
uv run python scripts/run_phase2_bounded_validation.py \
  --source /path/to/frozen-run \
  --target /path/to/validation-run
uv run python scripts/evaluate_phase2_bounded.py \
  --run /path/to/validation-run \
  --challenge eval/phase2_20260902_challenge.json
uv run python scripts/audit_phase2_bounded_validation.py \
  --run /path/to/validation-run \
  --workspace /path/to/independent-audit
```

Acceptance requires all normalized units to be judged, all challenge cases to pass, every Research
object to be independently reviewed, and stratified Watch/Archive samples to show no systemic
false-negative, false-positive, object-resolution, or source-bias failure. Local auditor findings are
corrected and re-evaluated; they are not hidden by aggregate route counts. The frozen manifest,
hashes, model/prompt versions, per-batch receipts, final Editor thread, evaluation output, and
independent audit output are retained together. These scripts and challenge fixtures are development
tools; production consumes only the compact validated Phase 2 contract.

## Automation smoke acceptance

```bash
uv run ai-digest automation-smoke
```

The smoke must prove:

- a real Phase 1 runner writes and seals the fixture;
- the fixture covers X List, For You, GitHub, media, HN, and an arXiv/HF pair;
- literal U+2028/U+2029 and a referenced blob cross the JSONL queue boundary;
- every item belongs to exactly one unit, every unit has exactly one Phase 2 decision, and every
  Research unit belongs to exactly one concrete object;
- at least one research object exists;
- Phase 3 failures are zero and each intake exactly covers its object handoff;
- `DONE=complete`, not failure fallback;
- queue transition is `jobs -> completed -> archived`;
- publish preflight resolves every internal link;
- the in-memory Wiki has no empty pages and makes zero live Lark calls.

The receipt is written below the chosen smoke root as `automation_smoke_receipt.json`.
It is evidence for integration and recovery only, not Phase 2 semantic quality.

## Failure injection

Tests include:

- legal JSON strings containing Unicode line/paragraph separators;
- interrupted enqueue and visible-job recovery;
- Phase 2 per-batch session checkpoints, duplicate/missing-ID repair, Editor recovery, and stale-output
  rejection;
- Phase 3 intake/evidence closure with optional subreports;
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
2. Phase 2 unit/decision/object exactness, bounded Reader receipts and the final Editor thread ID;
3. Phase 3 `failures.json`, `quality.json`, and each research manifest;
4. Brief links and Wiki year/month/day/main-report/subreport hierarchy;
5. Lark `publish_manifest.json`, real message/chat IDs, and no duplicate DM;
6. queue directories empty or in an explained retry state;
7. log modification times after the installed snapshot timestamp.
