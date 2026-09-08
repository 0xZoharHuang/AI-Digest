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

## Previous release decision: NO-GO for the proposed Phase 2 question partition

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

## Approved boundary correction: grounded objects, independent research tasks

The user approved removing forced research-question subdivision. `grounded_objects_v1`
now preserves the upstream grounded object/event partition, IDs, labels and memberships.
The former question organizer only attaches identity/fingerprint history context; it makes
zero model calls and never re-partitions evidence. Canonical paper grounding, exact article
identity and missing-context abstention remain. Phase 3 owns research questions and depth.

Fresh full-day cache replays (unchanged imported policy hashes, zero new model calls):

| Day | Normalized information | Packages | Singleton packages | Largest package |
| --- | ---: | ---: | ---: | ---: |
| September 6 | 3,217 | 2,605 | 2,529 | 167 |
| September 7 | 3,098 | 2,508 | 2,433 | 141 |
| September 8 | 3,766 | 2,986 | 2,858 | 126 |

These distributions do not prove perfect classification: they show why coverage should
also improve through independently tracked multi-package research rather than forced
large-group merging. September 8 budget-zero replay has exactly the same five Phase 2
artifact hashes. The 20-package precision regression completed with 20 supported decisions,
20 recognized signals and zero cross-package claims in the source-grounded draft review;
manual precision checks remain necessary alongside that model review.

New object-level pair reviews explicitly accept grounded shared objects as well as shared
events/questions, but not a generic company/domain relationship. They retain the old relation
labels and evidence, and record the changed acceptance contract. Previous failed strict
question-partition reviews remain unchanged; new results are not comparable population
accuracy estimates. Deployment is still gated on these reviews, joint dynamic admission
and the final no-publish pipeline smoke.

## Production selection: dynamic Phase 3, stable Phase 2 partition

The additional full-source grounding review did not reliably beat the installed
object/event partition. The final candidate's September 7 fixed diagnostic had 24
disagreements, equal to the baseline's 24, with different errors. These are diagnostic
pairs, not population accuracy. Stronger verification is not proof of a better aggregate
partition; do not enable `phase2_evidence_packets` in this release. The forced question
organizer has been removed from code, and further grounded-object experiments stay gated.

Production configuration selects `phase2_evidence_packets=false`,
`phase3_dynamic_tasks=true`, `phase3_task_max_packages=20`, and 15 daily research tasks.
Existing Luna/medium labels, Sol/high aliases and admission, Sol/medium research and
Terra/high brief profiles are preserved. Priority/exploration are selection roles rather
than fixed 12+3 worker pools. Large cases can occupy a whole task; small packages share
a thread but retain separate sources, decisions and reports. Phase 2 is never truncated
to match daily research budget.

The current-code stable Phase 2 September 8 cache replay reproduced all 2,869 package IDs
and memberships, labels.jsonl, units.jsonl and catalog.jsonl byte-for-byte. The sole package
label difference is normalized DOI spelling (`doi:10.1073/pnas.2520070123`), not membership.
The experimental joint allocation selected 84 packages / 217 information units into 15
tasks (16 exploration), compared with the old day's 42 packages / 174 units. This is planned
coverage, not completed reports, and is not the final stable-partition allocation receipt.

Current full suite: 289 tests, 86.63% measured core coverage, Ruff and Mypy passed.
The no-publish dynamic smoke completed 6/6 package outcomes (2 reports, 4 not-published),
with no failures, six non-empty Wiki pages, no unresolved internal links and queue traversal
through archive. Its Phase 2 artifacts also match a current-code deterministic replay.
Installed-runtime checks and stable-partition joint admission remain the cutover gates.

### Cutover result

Deployed `app-a791e341c5cc-20260908T111433Z` locally on September 8. Stable-partition
joint admission selected 88 packages / 247 normalized information units / 414 observations
into 15 tasks (16 exploration; actual task sizes 1–8). This is planned coverage, not 88
completed reports. Installed no-publish smoke passed: 6/6 outcomes, 4 reports, 2 not-published,
zero failures, eight non-empty Wiki pages, valid links and complete queue traversal.
Installed doctor, real launchd Codex/Lark authentication, exact-reference read-only probe,
module byte comparison and budget 0-versus-15 Phase 2 hash equivalence passed.

All three loaded services point to the new snapshot, with exit 0 and empty active queues.
Rollback is `app-6beac11858bf-20260906T143722Z`; retention deleted no snapshots. The detailed
local receipt is under `deployments/20260908-dynamic/acceptance.md` in the runtime root.
Natural next-day editorial quality and duration remain unobserved. Experimental Phase 2
evidence routing stays OFF: the new grouping candidates did not establish stable superiority
over the existing partition, so the release must not be described as solving all Phase 2
classification errors.
