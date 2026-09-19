# Phase 2 stateful Jev release — 2026-09-20

Implementation: `stateful-choice-v1.3`; public artifact contract: `jev_reading_v3`.
Deployment is gated by installed `release_acceptance.json`, not this document.

## First-principles contract

All N Phase 1 originals stay intact. Jev independently determines information
state, exclusion review confirms R empty records, and every other original belongs
to exactly one of M research folders. M does not depend on the Phase 3 budget.
An unresolved record is retained, not treated as worthless. Phase 2 makes **zero
Codex calls**. Candidate embeddings use local Qwen3-Embedding-0.6B, not a paid Codex
reader. No stronger-model fallback is allowed.

```
Phase 1 immutable originals + captured context
       -> Jev signal questions / exclusion review
       -> original-level hybrid neighbour index (no graph-approved grouping)
       -> disjoint local batches, at most four current packages per batch
       -> Jev chooses a complete local partition from legal options
       -> proposed new members each receive a grounded membership judgment
       -> code commits the whole wave, then updates candidate package ownership
       -> exact original-ID partition and sealed public artifacts
```

Four current packages have 15 possible partitions; the model can choose two
coherent pairs instead of accepting/rejecting one mixed bucket. A proposal does
not authorize transferring a neighbour's facts into an original. Incoming materials
must independently establish their shared reading direction. Large comparisons
use explicit original fragments plus an established package's anchored evidence;
context windows are not final package-size limits.

There is no whole-corpus fixed number of rounds. Independent batches execute in
parallel against one state; mutations are committed only after that wave finishes.
Unchanged package-version pairs are consumed once; every accepted mutation reduces
package count. A stalled/provider-failed wave cannot partially commit. The engine
uses one checkpoint plus durable request receipts, not a second agent or memory
controller. No generated topic taxonomy, summaries or research questions.

## Semantic validation and rejected candidates

The first stateful candidate wrongly merged an independent-journalism article with
world-model papers. A whole-group confirmation then still admitted a contextless
reply. Those candidates were rejected. Per-original membership checks prevented
that leakage, but their option definitions were too narrow for the user's approved
cross-project groupings. After the original two corrections, the user explicitly
approved **one additional targeted option-boundary revision**. No model change or
expanded architecture was made in that last revision.

The final criteria explicitly allow complementary datasets, policies, evaluation
and tools within one concrete research direction, while rejecting generic field,
company, author and incidental-word overlap. A different purpose/project alone is
not a reason to split.

Evidence under the runtime `validation/` directory:

| Test | Observed final result |
|---|---|
| Same 50 real originals (`stateful-auto50-20260920-r3`) | 48 packages, 0 exclusions; three explicitly Astra-related originals together |
| 14 boundary originals (`stateful-regressions-20260920-r3`) | All six assertions passed, including quoted release, weak signal, injection and cross-project VLA |
| Eight actual Phase 1 records (`stateful-real-boundaries-20260920-r3`) | VLA tooling/policy together; CUA-Universe/EvoCUA together; unrelated journalism/math separate |
| Same eight with permuted aliases/order (`...-r3-permuted`) | Same final memberships after mapping back to original IDs; six assertions passed |
| 6,000 / 60,000 mock originals | Exact ownership, bounded waves, permutation and completed replay; zero external model calls |

These are development/regression cases, **not an independently labeled held-out
benchmark**. The 50-source sample is unusually dispersed and does not establish
large-corpus density gains. It is wrong to treat its 48 folders as evidence of
48 independently verified unrelated topics. Singletons preserve uncertainty.

## Cost: full successful logical workflow, not just first reading

| Same frozen 50-source input | Earlier frozen candidate | Selected stateful candidate |
|---|---:|---:|
| Successful logical requests | 181 | 139 |
| Questions | 281 | 182 |
| Input tokens | 502,310 | 520,179 |
| Output tokens | 11,815 | 18,991 |
| Provider-reported market USD | 0.021097020 | 0.021847518 |
| Provider-reported successful actual USD | 0 | 0 |

Input and market cost increased about 3.6%; requests fell about 23%. This is not a
claim that every metric got cheaper. The selected run had five failed attempts
with unknown billing. The other evaluation runs, rejected candidates and transport
diagnostics are additional development usage, not included in this comparison.
Zero returned actual cost is not a promise of free future service. Cached-input
tokens were not reported by this provider. This is not a matched cost comparison
with a full historical production day's differently prepared input.

The selected 14-boundary test used 37,272 input / 3,113 output tokens, with three
failed attempts. The eight-real-record test used 50,449 / 1,433, with one failed
attempt; its permuted run used 46,906 / 1,426, with no failed attempts.

Mock 6k: 300 packages, 12,150 simulated calls, three grouping waves, approximately
117 MB maximum RSS. Mock 60k: 3,000 packages, 121,500 simulated calls, three waves,
approximately 534 MB maximum RSS. This excludes actual retrieval construction,
provider latency, failure rates and semantic quality; it is not a production SLA.

## Reliability and deployment

- Request bytes are canonically serialized, matching cache identity; Unicode line
  separators remain one JSONL line. Canonicalization is not claimed to cure provider
  503s: upstream TypeSafe/Gateway unavailability was observed independently.
- One explicit bounded request-retry layer journals each returned transient failure,
  backs off with jitter and respects Retry-After. Ambiguous EOF/timeout billing is
  retained and requires explicit queue recovery, not an invisible extra request.
- Jev queue retries distinguish genuine durable forward progress from repeated
  stalls. Recovered Phase 2 retries cannot exhaust Phase 3's separate retry episode.
  Authentication/quota failures never turn into semantic answers or auto-recharge.
- Old incomplete frozen-draft jobs require their old snapshot. Completed historical
  artifacts stay readable; no historical migration or in-flight semantic switch.
- The production entrypoint is `phase2_jev -> phase2_stateful`, not an evaluation
  script. Legacy experiment modules do not execute for new production jobs.
- A regression forbids all CodexRunner calls during actual Phase 2 dispatch. Doctor
  also skips obsolete Codex Phase 2 profiles under Jev, even with legacy flags set.
- Install a non-editable pending snapshot; run the real background identity with
  its WorkingDirectory explicitly set. Isolated acceptance uses one 5.6-sol research
  task, existing 5.6-terra brief, queue recovery, exact artifact import, publication
  preflight and local Wiki replacement. No real Wiki or direct message is sent.
- Cut over only after matching installed acceptance, idle services and empty queues;
  preserve the previous snapshot. Daily 7:00, recipients, Wiki and production Phase 3
  budgets/models stay unchanged. Natural next-day execution is additional evidence.

Remaining limitations: bounded neighbour recall; incomplete source/media context;
anchored rather than all-pairs validation for large folders; model/version and order
sensitivity outside the small regression corpus; unexposed underlying Jev version
behind the Gateway alias; and provider availability. No global optimum is claimed.
