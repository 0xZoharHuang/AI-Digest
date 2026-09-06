# Independent long-tail research batches

Status: implemented; isolated acceptance and immutable installation in progress.
**Not deployed.** Current production remains `08f1bcd5770a`.

## Contract

- Keep 07:00 start and target delivery before 09:00; do not enforce a token budget
  or truncate research merely to meet a clock target. Publish one complete daily brief.
- Keep Phase 2 immutable. A batch is an execution container, not a semantic package.
- One Codex thread receives every package file up front. It autonomously orders its
  investigation and chooses depth/publication, without per-package agent launches.
- Shared reader/method/catalog/history context is stored once. Each package has its
  own source, intake, evidence, decision and optional independent report/subreports.
- Every package must terminate with a validated report, a concrete not-published
  explanation, or a visible failure. Pending is not a low-value judgment.
- `phase3_tail_batch_size=1` preserves existing production behavior. Candidate sizes
  are 10/20/30/40, with 256,000 original-document bytes per batch and at most three
  tail batches. `phase3_tail_parallel_pool` compares shared concurrency three with
  three priority workers plus three tail workers. No model change is bundled in.
- Admission schema v2 counts execution jobs separately from selected packages;
  legacy v1 remains readable. Batch membership and sampling survive queue-directory
  moves. Valid canonical results survive retries; input mutation fails closed.

## Development evidence

- Latest source regression: 265 passed, 86.92% measured core coverage, Ruff/Mypy clean.
- Frozen development sample: 40 unselected, readable 1–3-unit packages across X,
  GitHub, HN and technical/robotics articles. Both inputs and the source identity are
  retained under runtime `validation/phase3-tail-batch-20260906`.
- Size 10 baseline: four threads completed all 40 packages without structural errors.
  Recorded usage: 10,603,197 input, 9,954,560 cached input, 82,712 output tokens;
  ordinary input proxy is 648,637 tokens. These are not subscription billing weights.
- First ten-package preflight: 474.77 seconds, six reports and four not-published
  decisions, one thread. The subsequent 658.34-second invocation reused that batch
  and ran the other three; it is **not** fresh four-batch end-to-end latency.
- Source-only blind reference: 12 concrete, nine weak, 12 no-signal, seven uncertain.
  This is a model-assisted draft, not human-certified gold or a forced publication list.
- Initial report review lacked external source text; its flags remain preserved.
  A second review includes exact experiment-thread retrieval excerpts and hash-checked
  captured text. It recognizes all 40 signals/decisions without cross-package claims;
  mean grounding/information-gain/readability scores are 3.075/2.95/3.45 out of four.
  Several peripheral statements still need tighter source/uncertainty handling,
  particularly unsupported parent-post or media descriptions in rejection reasons.
- V1 size 20 completed 40/40 in 656.66 seconds: 7,928,238 input,
  7,522,176 cached, 54,153 output. Its cheaper result exposed a premature dismissal
  of an enabling battery signal and unsupported parent/media context assumptions.
- V2 explicitly separates missing supplied evidence from unavailable public evidence,
  prohibits guessing parent/media context, and checks concrete enabling relationships.
  Three targeted regression cases completed with corrected uncertainty boundaries.
- V2 disjoint holdout, size 10: 40/40 completed in 1,245.07 seconds with three workers;
  19,739,332 input, 18,855,168 cached, 103,621 output. Grounded blind draft review:
  40 supported decisions, 40 recognized signals, zero cross-package claims;
  grounding/information-gain/readability means 3.125/2.925/3.55. These are
  model-assisted evidence-backed judgments, not a guarantee of every factual detail.
- Matched V2 size 20 remains under evaluation; do not conflate its result with V1.
  No size 30/40 claims are made. Prefer the quality-passing smaller size for rollout
  unless the larger size demonstrates a clear advantage.

## Publication and navigation

- User approved the complete 14-operation private-Wiki migration. Four nodes created,
  ten moved; full rescan confirmed all 175 original nodes retained (179 total), with
  all expected parents and descendants intact. Private visibility/open-sharing closed
  confirmed. No ACL changes, deletions, historical report edits or live test DMs.
- Fixed home and history roots are configured by identity, not ambiguous year titles.
  Missing roots fail closed. Future months are direct children of history; old report
  nodes are retained even if regenerated content no longer references them.
- Home links to recent reports across month boundaries. Daily DM remains owner-only,
  links to today's Brief and the same Wiki home, and distinguishes packages from jobs.
- Brief separates substantive priority research from independent tail discoveries;
  machine-owned coverage/failure sections are appended exactly once.
- Full-catalog density admission: 2,493 candidates, 12 priority plus three batches of
  ten, deterministic replay and unchanged Phase 2 artifact hashes. Native evidence
  hints inform priority without modifying classification or treating popularity as value.

## Remaining acceptance

Finish the size comparison, independently review outputs, and validate on a second
sample/order. Keep critiques and uncertain source claims rather than silently fixing
reference labels. If grounding instructions change, use a separately versioned
holdout comparison so prompt improvements are not attributed to batch size.

Measure input/cached/noncached/output tokens separately, per-batch duration, report
quality, and daily critical-path estimates including publishing overhead. Prefer a
quality-passing configuration with projected total duration at most 110 minutes;
near ties favor smaller batches and lower concurrency. Estimates from existing
production stage timings are not a new-version cold-run SLA.

Before cutover: full regression, real mixed priority/batch smoke, publication preflight,
installed-package verification, background authentication, empty queues and rollback
snapshot. Do not publish experimental reports or restart today's production research.

The user authorizes the existing gifted Codex reset only when quota is nearly exhausted
and needed; no early reset or purchase is authorized.
