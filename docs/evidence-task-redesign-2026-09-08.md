# Evidence organization and dynamic research tasks

Status: development, not deployed. Production remains immutable `6beac11858bf`.

## User-approved objective

Improve Phase 2 semantic retention/grouping and Phase 3 effective coverage and information
density. Remove the fixed 12-priority/3-tail partition; retain at most 15 top-level tasks.
Token usage, runtime and compaction are observations, not automatic rejection gates.
Preserve original evidence, independent package outcomes and the private Wiki/owner-only DM.

## Implementation boundaries

- `phase2_evidence_packets=false` and `phase3_dynamic_tasks=false` by default.
- Primary identity is distinct from the independently researchable question. Exact unique
  paper-title reposts can share canonical paper identity; comparisons and ambiguous titles
  cannot. Full normalized originals remain unchanged.
- Bounded credential-free short-link lookups add candidate identifiers, not merge authority.
  Resolved redirects are cached within the run; failures/deferred lookups do not erase signals.
- Reuse the existing semantic grouping as the conservative first stage, then split shared
  objects into question anchors. This intentionally retains the established baseline while
  the new boundaries are evaluated; no all-pairs graph or new orchestrator is introduced.
- Versioned `packet_context.json` is hashed alongside existing artifacts; package IDs still
  describe membership. Identity keys and evidence fingerprints provide separate continuity.
- Admission schema 3 adds `execution_batches` and `task_max_packages`. Exploration is a
  package role, independent of batch membership; schema 1/2 remain readable.
- Initial workload classes are brief/substantial/dedicated. Packing weights are 1/4/full
  task capacity, bounded by package count and original bytes. These are initial heuristics,
  not research-depth limits. Large originals get a dedicated task rather than truncation.
- Each task gets all assigned source files up front, one resumable thread and independent
  package outputs. The existing batch runner, outcome validator and recovery are reused.
- Historical reports are selected by run date, never file mtime. Read-only compatibility
  metadata is derived from old runs without rewriting them. Shared historical files are
  materialized and hashed, not just linked from an inaccessible index.

## Acceptance work remaining

1. Freeze/review Phase 2 development and held-out results from September 6–8, including
   confirmed paper repost misses, wrong-entity comparisons, weak signals and broad objects.
2. Compare dynamic task capacities 10/20/40 on the same originals, models and prompts;
   reverse representative batches and check late-package coverage and compaction behavior.
3. Source-ground reviewers must see the actual retrieved evidence, including non-web tool
   retrievals where present. Missing review excerpts are not proof of fabricated claims.
4. Require unchanged exact coverage, no unresolved critical semantic errors, no material
   loss of important research depth, and an actual increase in supported independent findings.
5. Full regression/coverage, mixed end-to-end pipeline, installed/background verification,
   then immutable deployment. Do not toggle production flags before these gates pass.

## Initial evidence

- Current-date production fixture confirms canonical recognition of arXiv + X pairs for
  `2609.04661`, `2607.19704`, `2502.09740`, without modifying source files.
- Baseline production on September 7/8 used 15 jobs for 42 packages per day. Token counts
  are not invoice amounts; retain cached/noncached/output counts separately.
- Initial complete test run: 275 tests passed but 84.25% measured core coverage, below
  the existing 85% gate. Added targeted tests rather than reducing the threshold.
- In-progress isolated experiment `validation/evidence-packets-20260908-full` began before
  short-link enrichment was integrated; treat it as an ablation, not final-version evidence.
- That ablation completed 3,766 units with exact coverage but FAILED semantic inspection:
  free-text question assignment emitted a 37-member catch-all and concatenated labels.
  Those outputs are retained, not edited. Question grouping now uses exact representative-ID
  assignments with bounded source cards and original-derived display titles; no free-text
  catch-all is permitted. The corrected full run is separate (`evidence-packets-20260908-v2`).
- The first dynamic admission ablation selected only 10 priority candidates and 30 exploration
  cases. It therefore did not meet the intended coverage/role balance. Dynamic ranking now
  selects a relative-priority candidate pool rather than a tiny final-publication shortlist,
  and packing constrains the realized exploration share. Earlier receipts remain unchanged.
- Additional tests restored the measured core coverage gate: 278 passed, 86.58% at that
  checkpoint. Re-run after subsequent code changes rather than reusing that as final evidence.
- Real no-model sandbox probe passed: external evidence files can be read, not modified.
  Their relative references and hashes remain stable when queue directories move.
- `scripts/evaluate_dynamic_admission.py` exercises full-catalog admission and deterministic
  replay without launching research or publishing any messages.

## September 8 validation updates (not deployment acceptance yet)

- Source checkpoint: 284 tests passed, 86.64% measured core coverage; Ruff/Mypy passed.
- Mixed pipeline with evidence packets and dynamic tasks completed 7 observations / 6 packages,
  4 reports and 2 not-published outcomes, zero failures, full queue import and Wiki dry-run.
  This proves integration, not full-day semantic quality.
- Wide-window dynamic admission (`bounded-catalog-v5`) selected 81 packages into 15 tasks
  from the unchanged 2,869-package baseline, with 14 exploration packages and identical replay.
  This is planned coverage, not 81 completed research findings. The earlier K+1-window trial
  was stopped because broad K caused very slow shortlist reduction; all receipts retained.
- Same 40-package capacity experiment: size 10/20/40 all completed exactly 40 independent
  outcomes. Source-grounded blind draft scores (grounding / gain / readability):
  10 = 3.325 / 3.2 / 3.825; 20 = 3.15 / 3.125 / 3.8; 40 = 3.125 / 3.175 / 3.775.
  Size 10 and 20 had no unsupported decisions, signal misses or cross-package claims in that
  review. Size 40 had one readership-relevance disagreement; its reverse-order run additionally
  produced an unsupported capability claim and missed supplied quoted context. Do not deploy 40.
  Size 20 reverse-order review remains a gate. These small diagnostic scores are not universal
  quality estimates, and concurrent development load makes timings unsuitable as a daily SLA.
- Phase 2's 81-pair question-boundary diagnostic had 3 uncertain cases; the initial candidate
  reduced draft errors from 34 to 14 but introduced avoidable splits of paper commentary and
  already-coherent events. The implementation now preserves those groups and skips redundant
  identity decisions on mechanically identified records; do not treat the old pair score as
  final-candidate acceptance.
- Label changes on terse replies exposed a retention boundary: missing parent/media/link
  context now forces abstention from a pure-chatter decision. `context_policy_version=2`
  validates this at sealing. This preserves uncertainty rather than inventing missing context.
- Current Phase 2 frozen runs are `evidence-packets-20260906-context-guard`,
  `evidence-packets-20260907-context-guard`, and `evidence-packets-20260908-context-guard`.
  Invocation receipts include the actual imported policy-file hashes before/after execution.
  Require unchanged implementation hashes before treating a run as final-version evidence.

### Capacity decision and manual checks

Size 20 reverse order also completed 40/40 and its source-grounded draft review reported
40 supported decisions, 40 recognized signals, no cross-package claims, and mean
grounding/gain/readability 3.4/3.225/3.775. Prefer a dynamic capacity ceiling of 20 for this
release; workload weights usually produce smaller real tasks and may dedicate complex cases.
Size 40 is rejected for this rollout because its reverse-order run missed visible quoted
context and introduced an unsupported capability description. Original failed outputs remain.

Manual comparison of JEPA, Dr Claw and Allura Memory reports found the 20-package outputs
largely preserved mechanisms and limitations while being shorter, but also caught a metric
precision defect the aggregate judge did not flag: Spearman rank correlation was called linear
correlation. General research-method instructions now require exact statistical terminology,
representative benchmark magnitudes alongside headline gains, and executable evidence before
claiming a system enforces a constraint. A separate 20-package precision regression places
known cases at beginning/middle/end positions; do not count this as a controlled batch-size trial.

The active final checks are Phase 2 context-guard runs for September 6–8, their semantic
review and retention audit, the precision regression, and the final mixed pipeline smoke.
Production is still `6beac11858bf`; no feature flags have been enabled in its configuration.

## Current release decision: NO-GO for the proposed Phase 2 question partition

Latest source checks passed: 286 tests, 86.68% measured core coverage, Ruff and Mypy.
All supervised experiments have finished or were explicitly stopped; no new production
deployment or live publication was performed.

The full-day diagnostics still show meaningful same-event evidence being separated: an
official research-acceleration report's metrics and responses, and reports/responses about
the same Wiki incident. Exact article URL/title normalization and unsupported paper-name
assignment guards are implemented and tested, but these local fixes do not establish that
the question-partitioning architecture meets the semantic acceptance gate.

Recommended boundary revision, awaiting user direction: Phase 2 should label signals and
aggregate grounded object/event evidence without preplanning or forcing research-question
subdivision. Phase 3 should own question selection, depth and report/subreport structure
inside independently tracked multi-package tasks. Keep the tested dynamic task machinery,
identity/context safeguards, immutable evidence and owner-only Wiki publishing; remove the
default requirement to split Phase 2 objects into research questions. Do not enable the new
production flags merely because structural tests pass.
