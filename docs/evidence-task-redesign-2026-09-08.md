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
