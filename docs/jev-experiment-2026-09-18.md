# Jev Phase 2 experiment — 2026-09-18

Status: connectivity proven through Vercel; low-rate real-data probe repeatedly limited by free-tier 429; paid access needs user approval.
No production Phase 2 engine, scheduler, model configuration, Wiki or recipients changed.

## Completed

- No TypeSafe key was available locally. Existing Vercel CLI authentication works for the
  personal `0xzoharhuangs-projects` scope. Gateway catalog lists `typesafe-ai/jev` as evaluation.
- Installed pinned `ai@7.0.106` and `@ai-sdk/gateway@4.0.86`; bridge uses evaluation,
  not chat completions, no generative fallback, SDK retries disabled, 45-second request timeout.
- Created key `ai-digest-jev-eval-20260918`, expiry September 25; secret stored in macOS
  Keychain under service of the same name, account `ai-digest`. Never put key in repo/output.
- CLI creation was passed a $10 non-renewing budget, but subsequent inspection reported
  **budget none**. Do not claim a provider-side cap. No team-wide budgets were changed.
- One synthetic connectivity call: 365 input tokens, $0.00001533.
- Four complete original records evaluated successfully, all `present`; fifth real-data call
  failed with 429: free-tier requests on this model require paid credits for unrestricted access.
- Recorded total cost $0.000285726. No credits purchased. Local conservative reserved
  budget $0.030 includes the initial probe and failed attempt; ceiling remains $10.

## Prototype safety / limits

`scripts/probe_jev_signals.py` replays original frozen records without production writes.
`scripts/jev_gateway.mjs` only calls Jev. `src/ai_digest/jev_probe.py` validates answer IDs,
choice membership, finite probabilities and provider/model; saves original request, result,
usage and cost to a separate validation folder. Failure retains its reservation and is not
silently retried. Oversize records are marked unevaluated rather than truncated or discarded.

The local budget lock serializes probe calls; reservation is $0.005 per <=24,000 UTF-8 byte
request. This is deliberately a small-sample harness, not the final throughput design.
Use the same budget-root for the entire experiment; do not create roots to reset its budget.
Initial connectivity expense is included explicitly. Failed calls need an explicit reviewed
resume implementation after account access is restored, not deletion of receipts.

Gateway response exposes only the `typesafe-ai/jev` alias, not `jev-1.13.0`. Its model directory
reports $0.042/M input, free output; observed invoice agrees. We cannot claim pinned weights
through this route. Preserve route/model metadata and resolve this gate before production.

No grouping-quality claims are possible from four successful labels. The prior 200-unit
holdout is only being reused for feasibility screening, not a fresh final holdout for the new
architecture. Full input-view cleaning, chunking, related-material clustering, exclusion
review, Phase 3 adaptation, three-day comparison and end-to-end acceptance remain unimplemented.

## Follow-up evidence

The first rate-limited call succeeded after a delayed retry. The failure receipt was preserved
under `attempts/` and its reservation retained. At 20-second spacing, the probe progressed to
9 successful original records, then received the same free-tier 429 again. This is not a
permanent authentication failure, but free-tier throughput is not suitable for claiming the
full planned evaluation can run promptly. No repeated rapid retries or new keys were used.
Latest model-priced usage is $0.000604002; conservative local reservation is $0.060.
This used free credit, not a card charge. No model experiment is still running.

Browser checkout confirmed minimum top-up **$10 credits**. The preview total was **$11.53**
including $0.59 processing and $0.94 estimated tax. An $8 custom amount was rejected with
“Minimum amount is $10”. Auto-reload is disabled. The Pay action also accepts Vercel AI
Product Terms. **No payment submitted**; authorization is required before exceeding the
approved $10 cash budget or accepting those terms. The payment UI was not bypassed.

Tests: full suite 305 passed before three additional budget/cache/oversize tests; all eight
Jev-specific tests now pass, Ruff and targeted mypy pass. This is prototype safety validation,
not classification or grouping acceptance. Production runtime config is unchanged.

## Resume

### Payment approved and completed

User explicitly approved $11.53 and the linked AI Product Terms in the following turn.
Submitted the existing checkout once at $11.53; Gateway balance changed from $5 free credit
to $15 credit. Purchased $10 credits, auto-reload remains disabled. Experiment inference
budget stays $10 (not $15); unused credit is not permission to expand the experiment.
Resuming the same saved requests with explicit 429 retry, retaining historical attempts.

### Post-payment verification

Gateway API Keys UI subsequently displayed `$0.0047 / $10 spent` for the exact experiment
key. Thus the earlier CLI `budget none` output did not accurately expose the configured
cap. Keep both observations in the journal; UI now confirms a $10 key budget. Local
reservation protection is retained independently. No team-wide budget was changed.

Initial 24 original-record calls completed: raw model output 23 present/1 chatter. The
chatter item was a wave replying to an uncaptured parent; the conservative program guard
retains it as unclear. The old model reference also called it chatter, illustrating why
model agreement is not enough to accept exclusions. Extending to the existing 200-item
set is feasibility screening, not a new untouched semantic holdout.

All 12 diagnostic relation pairs completed (11 together, 1 broad_only). One worrying
example scored together=.96 for an Astra game-generation comment versus Perplexity's
Astra integration announcement. **The user explicitly approved these in the same pack**
in this turn: different matters about the same named model can be jointly read and split
into subreports by Phase 3. This is an accepted development example, not a false merge
under the new objective. Do not impose the old same-event gold labels on reading packs.
Two connection timeouts were recovered with cached successful results intact. Added a
single bounded three-attempt transient retry layer; each failed attempt keeps its receipt
and budget reservation, while SDK internal retries remain disabled.

Experimental `jev_grouping.py` implements deterministic centre-based grouping with at
most four member witnesses per candidate group; it prevents unconditional transitive
closure and does not generate topics or research questions. It is not connected to the
production router and has no empirical grouping acceptance yet.

The isolated `experiment_jev_packs.py` now connects original-record signal checks, Jev
exclusion confirmation, existing local Qwen/HNSW candidates, bounded identifier candidates,
Jev relationship decisions and numbered-pack assembly. It writes candidate artifacts only,
never PHASE2_COMPLETE, and verifies the original file hash before completion. First cohort
uses complete originals from the 12 development pairs; it is not a final holdout. Oversize
records are explicitly retained/unevaluated; lossless chunking is still an acceptance gate.

1. Enable paid Gateway access within the authorized $10 limit, or supply a TypeSafe API key
   securely for native version-pinned evaluation. Do not auto-top-up or change subscription.
2. Confirm the three source-grounded user boundary questions already sent (same model but
   unrelated news; research paper and its discussion; complementary evaluations of one model).
3. Use the implemented explicit 429 retry preserving the failed attempt receipt; continue small probes before
   building the complete candidate. Keep old production active until full acceptance.

Artifacts: `~/Library/Application Support/ai-digest/validation/jev-20260918/`.
Runtime reference remains 5.6 Luna/medium initial labels, 5.6 Sol/high merging and admission,
5.6 Sol/medium research, 5.6 Terra/high brief. No GPT-6 calls were dispatched.

### Scale-study follow-up (supersedes the pending steps above)

Payment, bounded retry and the development boundary decisions are complete. The user
also explicitly approved VLA dataset tooling plus MAE-Self-Evaluating-VLA in one reading
pack; same project/event is not required. The final scale policy is `reading`, with one
original SourceItem per input unit and existing captured quotes preserved.

See [the scale review](jev-phase2-scale-review-2026-09-18.md) for current measurements,
source-grounded cases, failed variants and acceptance status. The centre prototype and
global-graph experiment are **not** connected to production. The graph's 500-record run
found real cross-topic chaining despite cheap Jev judgments, so no semantic-success or
deployment claim is justified. Cached offline witness assembly reduces chaining but can
oversplit; it is only a diagnostic. Do not restart purchases or use a stronger fallback.

Earlier simultaneous atomic variants stopped with ENOSPC and are excluded from completed
benchmark claims. Subsequent large runs are sequential, embedding batch size8, with
per-call durable receipts and phase-end aggregate snapshots. One returned receipt was
validated and its missing cost settled once; eight no-result requests remain conservatively
reserved. No unrelated storage was deleted and no production task was interrupted.
