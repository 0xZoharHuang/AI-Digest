# Phase 2 quality follow-up — 2026-09-06

Status: semantic, cost, large-package review and installed integration gates passed.
**Deployed locally: `08f1bcd5770a`.** See [the deployment record](release-quality-2026-09-06.md).

## Current continuation checkpoint

Installed snapshot `e0c6c2e1681f` reproduced all four frozen Phase 2 artifact hashes
with zero model calls and passed the unchanged semantic gate. Its real installed
smoke completed three reports with zero failures, successful queue traversal and
publish preflight, and zero live Lark writes. Background Codex succeeded; the first
Lark probe failed before parsing stdout, then two bounded retests succeeded without
account/config changes. The initial auth failure was not reproduced; logs are retained.

A final deterministic accounting fix also supports historical `objects.json` and
legacy package membership fields, and rejects dangling admission IDs rather than
printing negative counts. It does not alter any Phase 2 algorithm or model prompt.

The accepted frozen semantic candidate is `phase2-quality-frozen-20260906-bounded-events`:
6,878 units, 5,813 packages, retention 193/195, same-object 110/113, separation 118/118,
explicit challenges passed. The unchanged reference's three remaining misses are versioned
Fable feedback, an unnamed multiview-video discussion, and an agent-anthropomorphism discussion.
They remain errors; no reference change was used to pass this full-corpus gate.

Recorded logical usage is 17,220,479 input / 624,420 output tokens: input +8.45%,
output +27.06%, aggregate +9.01% against the same frozen baseline. This passes the
10% aggregate token-increase guard, not a dollar-cost claim. Cache-only replay and
cached input counters must not be described as cold throughput or extra token savings.

September 6 replay (`phase2-quality-20260906-bounded-events`) yields 3,217 units and
2,493 packages; its five packages with at least ten units contain 174, 12, 11, 10,
and 10 units. It records 8,533,777 input / 314,620 output tokens. No historical run
or published report was overwritten. Many independent repositories/posts remain
singletons; package-count reduction is not the acceptance objective.

The separate 60-pair fragmentation diagnostic preserves its original 9/10 same and
26/26 different result. Blind discrepancy review mixed with controls plus direct
original-payload inspection identifies three uncertain relations: an unversioned
Gemini Flash comparison, an unnamed terminology discussion, and an uncaptured-parent
investment reply. The reviewed diagnostic is 9/9 same, 24/24 different, 27 uncertain;
the baseline is 0/9 same and 24/24 different. All 20 adjudicated decisions are recorded,
including rejection of a false shared-repository claim (a common GitHub search-response
blob does not prove shared code). Original drafts remain untouched. The scoring tool
validates model receipts, original documents and explicit review decisions; this is
model-assisted evidence review, not human certification or a population accuracy claim.

All five large daily packages (217 members, seven full-input audit chunks) received
independent model review. Direct evidence review retained same-version application
demos and new-model comparisons rather than treating every subtopic caption as a new
package. Four sparse/mixed/future-looking conversations remain explicitly uncertain;
this is not a zero-error purity claim. The known Muse evaluation is in its separate
four-unit Muse package, and unrelated math rhetoric is a singleton, not an Astra member.

Final source-tree regression: 251 passed, 86.83% measured core coverage; static checks
and installer dry-run passed. Real isolated integration: six reports, zero research
failures, queue traversal succeeded, nine in-memory Wiki pages with no unresolved
links and zero live Lark calls. The exact model profiles and current authentication
passed doctor. Full daily catalog admission selected 15 packages (12 priority plus
three exploration), replayed identically, and preserved every Phase 2 artifact hash.

- Prior engineering regression: 250 passed, measured core coverage 86.83%; Ruff,
  mypy (35 source files) and `git diff --check` passed. This does not establish
  semantic quality or installed runtime acceptance.
- The frozen 6,878-unit global-name registry and balanced-evidence variants both
  scored 106/113 same-object pairs, 118/118 different-object pairs, 193/195 signal
  retention, with no explicit challenge errors. They fail both the 95% same-object
  gate and the accepted baseline's 110/113 no-regression requirement.
- A further event-evidence global variant regressed to 91/113. It is rejected;
  more context or a stronger model is not sufficient evidence of a better algorithm.
- The best balanced-evidence computation recorded 17,130,983 input and 604,482
  output tokens, including reused successful calls. The compact Sol/high alias
  stage accounted for 91,045 input / 10,407 output tokens. Bulk work remains
  Luna/medium. These counters are not a subscription invoice or cold-runtime claim.
- A broad prefix-blocking trial was stopped after its plan expanded to 1,488
  names / 18 calls. Its partial outputs remain preserved. The bounded alternative
  only connects already shared naming ambiguities: 589 names / seven calls,
  at most 96 names and 128 KB of evidence per local call. Shared prefixes only
  propose comparisons; they never authorize a merge. A global compact view is now
  limited to at most 96 names, not a growing all-corpus context.
- The bounded event trial and isolated real-Codex end-to-end smoke completed.
  The smoke is publish-preflight only; it does not send live Lark messages.
- New safety checks cover selective ambiguity review, bounded retry/cancellation,
  oversized records, bounded alias registries, and the alias model's doctor profile.
  Phase 4 cannot seal a result after normalization loses a required report link.
  Article retry handling cannot mistake repeated metadata-only failures for recovery.

Acceptance follows task-specific evaluation, preserved failure examples, original
evidence review and separate semantic/engineering gates, consistent with
[OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
All references here remain model-assisted, not human-certified labels. Original
drafts and uncertain judgments are retained; aggregate scores must not be improved
by silently rewriting a reference.

## Confirmed observations

- September 6: 3,217 normalized information units, 3,112 candidate units, 2,635
  packages, 2,477 singleton packages. Fifteen researched packages covered 199 units.
- 319 HN units contained only empty tombstones; 300 were singleton packages.
- GitHub contributed 816 singleton packages from 822 retained units. The source
  really contains many independent projects; reduction in package count is not a goal.
- A production Astra package included a thread primarily evaluating Muse Spark.
  This is a semantic boundary challenge, not evidence that all large packages are bad.
- Direct and existing local-proxy requests for two failed articles both returned 403.
  No proxy or browser workaround has been enabled.

## Correction to the initial diagnosis

`constrained_components` did not itself ignore singleton abstentions. However,
`validate_identities` already removed those singleton groups before production
aggregation. The defensive fix and regression test are worthwhile, but **must not
be claimed as a demonstrated cause of production fragmentation**.

## Changes

- Versioned eligibility metadata excludes empty deletion records from research
  candidates without deleting raw observations or changing weak-signal labels.
  Historical artifacts remain readable; completed runs are not rewritten.
- Same-object consolidation now explicitly distinguishes a material's primary
  subject from an incidental mention, comparison baseline, or quoted subject.
  Noncanonical representative chains/cycles are rejected and repaired rather than
  silently interpreted as transitive identity. Historical sealed artifacts remain readable.
- Default daily budget remains 15. Priority prefix takes 12 places; deterministic
  source-balanced sampling takes up to 3 from readable, unselected 1–3-unit packages.
  No extra model call is needed for sampling. Unused exploration slots fall back to
  the already ranked priority list. Model selection may still leave budget unused.
- Date/input/version-derived seed, selected exploration IDs and stratum counts are
  saved with admission. Replay uses hashed receipts; one package still maps to one lead.
- Deterministic counters distinguish observations, information, packages, scheduled
  coverage and recorded reviewed coverage. The model does not own global counts.
- Article failures are categorized and retained, with persistent retry delays of
  1, 6 and then 24 hours. Deferred bodies remain visibly partial, not silently healthy.
  Bounded error responses are retained. A successful recovery clears the retry state.

## Validation and release gates

- Unit/integration tests include empty deletion vs unavailable content, singleton
  abstention, stable sampling, all/zero budgets, replay, Phase 2 immutability, counts,
  HTTP failure categories and durable retry/recovery.
- `scripts/audit_phase2_quality.py` freezes 120 stratified singleton cases and 120
  proposed pairs across September 3–6, with original input and blind reviewer receipts.
  Its outputs are **model-assisted drafts**, not human-certified labels.
- `scripts/score_phase2_quality_audit.py` binds reviews to original inputs and sealed
  output hashes; uncertainty remains explicit. No post-hoc relabeling to improve scores.
- Full September 6 replay is isolated under the private validation directory and
  reuses annotation/embedding caches; no collection, research leads or publication.
- Do not switch production until semantic errors, all large-package membership audits,
  cross-day regression gates, cost comparison and installed smoke are complete.
- During isolated experiments, the prior production snapshot and published reports were not modified.

## First full-corpus result (not accepted)

September 6, primary-subject prompt, 256-card comparison cap:

- 3,217 units, 2,399 packages, 319 empty deletion records excluded from candidates.
- 265 logical calls: 137 reused annotation/confirmation calls and 128 executed
  identity calls. The run took 859.63 seconds **with caches**, not cold throughput.
- Recorded input 8,028,495 / output 267,727 tokens, versus baseline input 8,130,435 /
  output 272,951. Development reviewer usage is separate from this production estimate.
- The first 240-case audit was heavily populated with unreadable cross-package
  pairs. It was retained unchanged; a separate 60-case readable cross-package
  supplement was collected rather than silently replacing the original sample.
- Supplement: 10 same-object, 26 different-object, 24 uncertain model-assisted
  judgments. Baseline joined 0/10 same-object pairs; candidate joined 2/10.
  Both separated 26/26 different-object pairs. **This is a targeted failure set,
  not a population-wide 20% accuracy estimate.** It fails the agreed release gate.
- All ten same-object pairs co-occurred in model comparison scopes. Five had at
  least one positive merge decision; overlap constraints and inconsistent local
  decisions prevented some from joining. Increasing ANN recall is not a sufficient fix.
- The Muse primary-subject challenge moved from the Astra package to a benchmark
  package, not to a demonstrably correct package. Prompt-only change is insufficient.
- Seven baseline packages with at least ten members received full-member independent
  audits; these remain model-assisted drafts, not certified gold partitions.

Comparison scope cost estimates on this corpus (no additional model calls):

| Maximum cards | Calls | Card appearances | Evidence characters |
|---|---:|---:|---:|
| 32 | 481 | 12,101 | 5,353,686 |
| 64 | 260 | 10,540 | 4,656,658 |
| 128 | 146 | 9,187 | 4,093,884 |
| 256 | 128 | 8,121 | 3,638,893 |

The 128-card numeric-representative trial also failed: 2,376 packages, same-object
5/10, different-object 25/26. Recorded input 8,401,465 / output 300,445 tokens;
1,018.54 seconds with caches. Lowering the window cap alone is not sufficient.

Two bounded primary-name probes on the readable failure set scored 6/10 and 8/10
same-object; 25/26 and 26/26 different-object respectively. These are development
probes, not independent acceptance results. One disputed Gemini pair lacks an
explicit version on one side; the original reference and its original score are
retained rather than silently amended. Another probe failure confuses a naming
question with a symmetric product comparison.

The next isolated trial uses named primary-object votes instead of transitive
numeric representative unions. Each unit receives one stable owner; ties remain
separate. Literal primary repository identities prevent same-title projects from
being joined. This does not add a full-corpus reading pass or a stronger production
model. The mode is disabled by default until independently accepted.

An early named-mode attempt stopped on a formatting validation issue: the model
used legitimate `product:` and `event:` namespaces in addition to the examples in
the prompt. The validator now normalizes descriptive object namespaces, while
company-only/unknown labels remain isolated. Completed calls were preserved and
the isolated run resumed. This is not a production interruption.

Production defaults remain 256 cards and numeric mode until evidence supports a
promotion. No production cutover or historical report rewrite has happened.

The official evaluation guidance supports explicit task-specific tests, production
examples, edge cases and ongoing evaluation rather than accepting a successful run
as evidence of semantic correctness:
[OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
