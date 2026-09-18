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

1. Enable paid Gateway access within the authorized $10 limit, or supply a TypeSafe API key
   securely for native version-pinned evaluation. Do not auto-top-up or change subscription.
2. Confirm the three source-grounded user boundary questions already sent (same model but
   unrelated news; research paper and its discussion; complementary evaluations of one model).
3. Use the implemented explicit 429 retry preserving the failed attempt receipt; continue small probes before
   building the complete candidate. Keep old production active until full acceptance.

Artifacts: `~/Library/Application Support/ai-digest/validation/jev-20260918/`.
Runtime reference remains 5.6 Luna/medium initial labels, 5.6 Sol/high merging and admission,
5.6 Sol/medium research, 5.6 Terra/high brief. No GPT-6 calls were dispatched.
