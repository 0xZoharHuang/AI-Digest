# Jev Phase 2: source-grounded scale experiment

核心目标：完整保留 Phase 1 原始记录，让 Jev 判断可读信号与共同阅读关系，自然形成
数量不预设的资料包；不预设研究问题、不强求共同结论、不受 Phase 3 预算反向影响。
检索和程序负责把材料送到模型面前、记录并执行判断，不能用相似度或图连通关系替代语义。
本文件区分实验完成与生产验收；发现的候选漏供、上下文缺失和串行耗时仍是上线阻碍。

Status: isolated experiment, **not production accepted**. Production routing, models,
07:00 schedule, Wiki and recipients are unchanged. No Phase 3 research was dispatched.

## Objective frozen with the user

Phase 2 removes clearly empty/useless material and packages potentially useful original
material for Phase 3 to research. It does not invent research questions, conclusions or
topic names. Different releases, applications and opinions about one model can co-read.
Different projects in a focused direction can also co-read: the user explicitly approved
VLA dataset tooling plus MAE-Self-Evaluating-VLA. Neither same event nor same project is
required. A generic AI/software connection alone is insufficient. Phase 3 budget does not
enter screening or grouping.

## Official practices and what we used

- [TypeSafe reranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe):
  retrieve a bounded candidate set, then ask independent relevance judgments. Their
  example uses BM25 top 30 out of 3,565 documents; reranking cannot recover missing
  candidates. This supports bounded retrieval followed by semantic judgments, not
  keyword equality as the final grouping decision.
- [Building with System One](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)
  and [advanced primitives](https://docs.typesafe.ai/primitives/advanced): deterministic
  work belongs in code; relevant structured state and narrow typed questions belong in
  the model. Candidate originals can be supplied in each question's structured instructions.
- [Choice](https://docs.typesafe.ai/primitives/choice) uses known alternatives. We use
  present / unclear / chatter, not an invented predeclared topic taxonomy. Boolean/Noul
  handles the open-ended pair relation.
- [Entity alignment](https://docs.typesafe.ai/cookbooks/entity_alignment) is a closer
  decision-design analogy: a cheap shortlist precedes model judgment, and related/uncertain
  remains distinct from same-product. Its 450-pair demonstration uses ordered Score labels;
  our task deliberately allows broader reading bundles than duplicate products, so its
  merge semantics and published results cannot be imported as our acceptance evidence.
  [Confidence guidance](https://docs.typesafe.ai/confidence) also distinguishes an argmax
  answer from certainty. Noul has no separate confidence property; our numeric confirmation
  threshold is not the SDK's distribution-derived Choice confidence.
- [Parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions) keeps its
  document state fixed. Our original shared-candidate batching changed the state and
  moved two of twelve diagnostic pairs across the threshold. Fixed anchor state with
  each candidate in its own question had zero crossings in twelve pairs, of which eight
  actually had multiple questions. This small diagnostic is not a universal guarantee.
- [Models](https://docs.typesafe.ai/models) and
  [known jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13): text-only,
  bounded context, literal-reading and distracting-state limitations. A media URL is
  not inspected media. The Gateway alias does not expose a pinned native weight version.
- [Vercel evaluation API](https://vercel.com/docs/ai-gateway/modalities/evaluation):
  AI SDK evaluation, not the OpenAI-compatible chat endpoint. Tested dependencies are
  `ai@7.0.106` and `@ai-sdk/gateway@4.0.86`; model is `typesafe-ai/jev` throughout.

Academic cross-check: [ClusterLLM](https://arxiv.org/abs/2305.14871) uses language-model
feedback to align clustering perspective and granularity with user intent.
[Cequel v2](https://arxiv.org/html/2504.15640v2) selectively queries pairs/triplets and
uses both must-link and cannot-link information. These support spending model calls on
semantic decisions rather than blindly trusting embeddings. They are **not** drop-in
proofs for our pipeline: Cequel's formal problem takes a desired/ground-truth cluster
count K, whereas our package count must emerge naturally; reading compatibility also
need not be a transitive same-class relation. We do not add embedder fine-tuning or a
constrained-clustering framework merely to imitate a paper.

## Tested architecture

1. Frozen source-stratified nested 50 / 500 / 1,000 samples from 4,405 real archived
   Phase 1 records on September 8. Each final input unit is one original SourceItem;
   already-captured quotes remain attached. Legacy conversation grouping is bypassed
   in the experiment because it can precombine distinct material before semantics.
2. Preserve originals. Remove nonsemantic collector metadata only from the model view.
   Existing deterministic eligibility rejects empty tombstones. Jev screens remaining
   records; proposed chatter receives exclusion review or is retained as uncertain when
   required parent/media context is unavailable.
3. Index all retained records, not independent chronological batches. Existing pinned
   Qwen3-Embedding-0.6B/HNSW top 8, bounded character-TFIDF top 4, plus identifier
   candidates. An identifier proposes a relation; it does not prove semantic membership.
4. Judge candidate pairs with Jev. Up to eight independent questions share one anchor;
   each question carries its own candidate. Conservative 24 KB request guard splits
   requests; oversize material is retained and explicitly marked unevaluated, never
   silently truncated. This guard is not a model-context-limit measurement.
5. Experimental weighted Louvain graph at score .65, resolution 1, deterministic seed17.
   Split disconnected communities; retain isolated records. Numbered groups contain
   original IDs, no generated topic or research agenda.

The shortlist bounds model comparisons near O(Nk), rather than putting all N originals
in one context or testing all N² pairs. It does **not** establish million-record capacity:
the current harness loads an archive in memory, candidate recall is not exhaustively
measured, and service quota, I/O and memory at that scale have not been tested.

## Frozen reading-policy graph control

Historical Codex reference was read directly from this archive's
`02_routing/phase2_manifest.json`: 4,405 Phase1 records became3,766 legacy units and2,869
packages. Its recorded logical usage is9,265,136 input / 1,555,712 cached input / 341,207
output tokens for341 Luna5.6 calls, plus125,450 input / 16,257 output for4 Sol5.6 calls.
156 Luna calls were marked reused. This is **not a fresh same-sample A/B run or a dollar
invoice**, and no subscription-quota usage is converted into invented API dollars.
Baseline conversation context and the new record-level sample inputs differ.

Projected onto the500 selected original IDs, the baseline had403 present,42 unclear,
9 chatter and46 empty records. Jev retained those nine chatter records (eight uncertain,
one present), predominantly reactions with missing media/parents. One was a cartoon-style
quoted joke classified present, which needs information-presence calibration rather than
an automatic quality-win claim. The baseline is a reference, not human gold. The archived
Phase1 run was itself partial, including article-body extraction failures; no claim is made
that it captured every potentially relevant source or that those historical failures remain
unchanged today.

| Original records | Retained | Empty tombstones | Reading packs | Largest pack | Pair candidates | Full successful-call cost | Input / output tokens |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 49 | 1 | 45 | 3 | 287 | $0.014329014 | 341,167 / 7,077 |
| 500 | 454 | 46 | 282 | 41 | 4,302 | $0.205306962 | 4,888,261 / 94,255 |
| 1,000 | 905 | 95 | 472 | 83 | 8,766 | $0.434126490 | 10,336,345 / 191,068 |

The 500 run retained 397 present and 57 uncertain records. Raw model outputs were
397 present, 47 uncertain, 10 chatter; uncertainty guards preserved the latter ten.
There were 312 records with uncaptured context indicators. **No semantic chatter was
ultimately removed**, so these results do not demonstrate successful noise filtering.
The 28 oversized pair candidates remained unjudged; no original record was dropped.
At 1,000, 787 present / 118 uncertain records were retained, 617 had uncaptured context
indicators and 76 pair candidates were oversized. The 24 raw chatter decisions were
retained uncertain. Again there were no final semantic exclusions. Warm elapsed time was
968.2 seconds. The six largest group membership/title reviews confirmed cross-direction
chaining; 13 within-pack judged edges were below .20. The cached witness diagnostic
produced 677 packs, largest8, without additional model calls.

Costs count all unique successful screening, exclusion-review and relation requests
needed by each run, including local cache replay. Provider cached input is unreported
(unknown, not zero). Failed/ambiguous attempts retain a separate budget reservation;
their exact billing is not silently treated as zero. Local embedding compute is not an
API charge. Warm-cache observed times were 24.7 s and 378.1 s; these are **not cold
daily-pipeline latency benchmarks**. Cumulative development spend also includes discarded
variants and diagnostics; the one-time $11.53 payment is not inference cost.

## Review: useful signal, but graph assembly fails acceptance

The reviewing coding agent inspected all 50 original materials and the membership and
source excerpts of all 32 multi-record packs in the 500 run, with fuller rereads of
ambiguous examples. This is source-grounded model review, **not independent human gold**.
Only the two explicit user decisions above are human calibration labels.
The nested samples are development/scale samples, not three independent held-out sets;
some 500/1,000 material had already appeared in earlier variants. Score .65 is a frozen
experiment setting, not an empirically calibrated 65% correctness guarantee. Exhaustive
pair recall, order-reversal robustness and independent human acceptance remain unmeasured.

Correct/useful examples in the 500 run:

- VLA dataset tooling and MAE-Self-Evaluating-VLA form one two-record pack, matching the
  user's confirmed reading granularity.
- Mixed Tail Processes paper and the social post of that paper co-read.
- MiniCPM5-2B capability, work-task evaluation and token-efficiency posts form one pack.
- MZ-Rain and WeatherNext precipitation forecasting co-read; this does not equate their
  claims, versions or evaluation protocols.
- HYDRA machine-monitoring adapters are grouped, as are 4D-flow MRI studies and modern
  stereoscopic-monitor discussions.
- A body containing only a period retains its captured recursive-improvement announcement;
  “interesting eval” retains the quoted evaluation. Missing media is not fabricated.

Failures that prevent deployment:

- A 21-record graph community mixes visual generation/reconstruction, molecular design,
  protein modeling and personalized LoRA. Local relatedness is not transitive.
- A 12-record community mixes agent memory, graph anomaly detection, federated learning
  and TPU kernel generation. These are not one focused reading direction.
- The 41-record Astra-heavy community admits unrelated song-to-lyrics material and
  broad side discussions. Large size is not evidence density.
- Seven already-judged within-pack pairs scored below .20. For example, the SfM event
  versus personalized LoRA scored .10, yet community detection placed them together.
  The problem is partly deterministic assembly ignoring negative evidence, not only
  model competence or the user's broad reading granularity.
- Some sparse-context pairs are overconfident; retaining an uncertain original must not
  be mistaken for knowing which bundle it belongs to.

## Conservative offline diagnostic

`scripts/review_jev_graph.py` reuses the exact paid judgments with the existing bounded
centre/witness assembly: centre, first/last and weakest linked member. It makes **zero
new model calls** and treats absent judgments as unknown. At 500 it produces 354 packs,
largest 7, versus the graph's 282 / largest41. This suppresses chaining but fragments
related Astra material and still admits some overly broad pair relations. It is a useful
diagnostic, **not an accepted final solution**. Pack count alone cannot select a winner.

## Decision and smallest next step

Jev is economically viable for this kind of bounded, numerous semantic judgment; it has
not yet proved an acceptable end-to-end Phase 2 replacement. Keep production unchanged.
Do not ship the pure community detector or disguise its lower pack count as better coverage.

The remaining direction is the same original-preserving screen + global shortlist
+ typed relation design, with **bounded bundle-membership verification** against original
representatives and boundary examples. Prior negative judgments are evidence to investigate,
not a mechanical veto against a later decision supplied with genuinely new context. Reuse
known evidence; ask Jev for missing membership evidence. Do not add a taxonomy, strong-model
router, generated summaries, research-question layer, or another independent agent tier. This
proposal still needs a frozen held-out run before selection. The subsequent direct
membership prototype below is experimental, not an accepted implementation.

### User clarification and direct Jev membership follow-up

The user reiterated that other algorithms should serve Jev, not override its semantic
judgments. The first-principles goal was restated in conversation: preserve originals,
retain useful/uncertain signals, and form naturally numbered focused reading bundles;
no imposed research questions, topic taxonomy, target pack count or Phase 3 budget.

`experiment_jev_membership.py` therefore reuses only originals, screen labels and the
retrieval shortlist, **not the paid pair judgments or graph communities**. Retrieval
proposes up to four existing packs. Each pack shows an unchanged fixed core original,
the nearest member and the most recent member (deduplicated, at most three originals).
Jev `Choice` selects a pack / new / uncertain. A proposed join can receive one independent
Jev Noul confirmation. Code implements these choices; it does not perform community
merging. Oversize candidate views are explicitly omitted, never silently truncated.

At 50 originals (49 retained): direct Choice produced43 packs for $0.010057404, but also
joined a contextless political insult and a military discussion, and skeleton-animation
versus website-animation material. Choice argmax can still be weak (.44 for the latter).
The independent confirmation rejected these cases. A .65 confirmation setting over-split
user-allowed same-model material (48 packs); the standard binary .50 boundary produced46
packs for $0.010290168, including one four-record Astra/Fable reading bundle. This threshold
comparison is development tuning, **not evidence of calibrated confidence or acceptance**.
The .50 confirmed policy is frozen for the 500-record follow-up; do not keep changing it
in response to that sample. Missing candidate recall and sequential routing order remain
explicit risks. Sequential request latency must also be measured, not hidden by token price.

The 500-record follow-up completed: 454 retained originals, 375 packs (341 singletons,
34 multi-record packs containing113 records), largest32. Full logical successful-call
cost including screening is **$0.104921880**, 2,498,140 input / 49,515 output tokens.
There were442 Choice calls plus136 conditional confirmations, 578 membership-stage
requests total. Warm-screen/cold-membership elapsed time was932.4 seconds. The experiment
explicitly omitted92 candidate-pack views to stay within the conservative byte guard;
eight records could not fit any candidate view and were preserved separately.

All34 multi-record pack memberships were reviewed using source titles/excerpts, with full
payload rereads for suspected context errors. VLA tools + MAE, CUA-Universe + EvoCUA,
federated-learning studies, distillation studies and MiniCPM evaluation posts now co-read
without the graph's large cross-field chains. Missing-context cases need care: the apparent
unrelated video comment links directly to `ScottStevenson/SuperAstra`; the local-voice
discussion actually describes near-realtime character-consistent voice generation. Source
rereads matter more than title-only judgments. Two old pair scores below .20 remain within
packs, but they are not automatically false merges: one concerns the same taco-stand
conversation, the other spans Astra-related material with a captured usage announcement.
At least the short VLM-output comment lacks sufficient visible identity for confident
assignment to the Astra bundle; its surrounding reference needs review.

Known fragmentation has two separate causes, not one model-quality number:

- WeatherNext vs MZ-Rain was a global candidate pair with Jev score.79, but its pack ranked
  eighth among ten available candidates; top-four truncation hid it from membership Jev.
- MINT vs TourPhysics was shown to Jev, but the membership choice preferred a new pack
  (.81 versus .07 for TourPhysics). This is a task/context-dependent semantic disagreement.
- The Astra art post had no direct retrieval edge to the earlier evaluation post; it later
  formed another two-record Astra pack. A global index alone does not guarantee recall.

An offline diagnostic against prior positive pair judgments found86 split pairs whose
target pack missed top4, 34 whose view was omitted for context, 58 declined by membership,
and77 with another choice/confirmation outcome. These are **model-task disagreements, not
255 proven errors**. They identify where to review. A .50 rounded confirmation tie was
accepted in two development cases; this is not evidence of certainty.

The confirmed policy is also run unchanged on1,000. No further prompt or architecture
branch is introduced during that run. Before production, replace candidate dropping with
bounded candidate batches evaluated by Jev, audit retrieval misses, and address serial
latency. Those are necessary improvements in how code supplies evidence to the model,
not permission for a clustering algorithm to override its decision. The serial prototype
is a quality/cost experiment and is not the final daily-production scheduler.

The1,000-record direct-membership run is now complete. Same policy, no mid-run input or
transport changes:

| Original records | Retained | Packs | Singleton packs | Largest pack | Full logical successful-call cost | Input / output tokens |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 49 | 46 | 45 | 4 | $0.010290168 | 245,004 / 5,047 |
| 500 | 454 | 375 | 341 | 32 | $0.104921880 | 2,498,140 / 49,515 |
| 1,000 | 905 | 730 | 654 | 47 | $0.223262130 | 5,315,765 / 100,239 |

At1,000, 76 multi-record packs contain251 originals. There were882 Choice calls and327
conditional confirmations (1,209 membership-stage requests). 185 candidate-pack views
were omitted for context;19 originals had no fitting candidate view and stayed separate.
Elapsed time was2,010.8 seconds (33.5 minutes), with screening reused and one-shot serial
membership transport. This is unacceptable as an unmodified production driver at much
larger daily volumes; the five-record transport probe below does not constitute a fix.

All76 multi-record memberships were inspected through titles/source excerpts; suspicious
cases received original-payload rereads. Correct groupings include the user-approved VLA
pair, memory research plus a memory benchmark, prompt-injection research, MiniCPM evaluation
versions, a Bitcoin sidechain incident and its analysis, and software proof/verification
work. The largest Astra bundle has47 records, not47 independent sources. The prior
cross-field graph chains are absent in these inspected groups, but quality is **not
accepted**: for example `@OpenAI No` was assigned to the Astra pack with confirmation.65
even though the shown pack materials did not contain its missing parent. This is an
ungrounded membership decision, not a reason to discard that original. By contrast, a
Roblox-stock comment looked unrelated until its captured Astra game-generation quote was
read; that grouping has visible support. No prior pair score below.20 was within a final
1,000-case pack, demonstrating that this numeric check alone misses real quality problems.

Archived Codex pack assignments projected onto the selected originals provide context,
not a controlled quality comparison: at500 they cover445 records in380 packs (359 singles,
largest31); at1,000 they cover878 in719 packs (668 singles, largest65). Baseline had the
full day's context and different exclusion/grouping semantics. The Jev candidate retains
more uncertain material but has not demonstrated a dramatic density improvement or
superiority over Codex. No fresh Codex reference inference was purchased.

Final cumulative known successful experiment cost is **$1.628697210000**, including
discarded variants and the SDK probes. The conservative ledger budget occupancy is
$3.128217480000 out of the authorized$10; it includes historical failed/unsettled reserves
and is not an invoice total. The payment of$11.53 for credits is separate. Neither number
is hidden inside a favorable single-stage cost claim.

An additional read-only context audit found149 uncaptured quote/reply references in the
500-record sample; **29 already have readable parent records in the same 4,405-record
archive**. They can be supplied to Jev through deterministic ID lookup without fetching
new web content or forcing a conversation into one pack. This is a concrete input-supply
improvement, not a new classifier. It was not silently added to the frozen runs. Archived
SourceItems are preserved completely, but an article SourceItem can contain only a stored
preview plus a full-text reference; these experiments do not dereference every full article
or inspect media. “Original record unchanged” must not be read as “all linked content read.”

### SDK transport diagnostic (no semantic-policy change)

`benchmark_jev_transport.mjs` sends the same five short source-record screening requests
with fresh child processes versus one reused SDK process/client. The order was then
reversed (fresh/reuse, reuse/fresh), giving20 actual provider calls. Every underlying
call and cost is retained inside four aggregate budget receipts. Total additional cost:
$0.000605136; input tokens were3,602 per five-call group in both modes.

- Fresh subprocess calls: 1.17–1.35 seconds each across both repetitions.
- Reused client/process: first call1.13–1.26 s, subsequent calls0.45–0.56 s.
- All five categorical choices matched in all repetitions; probabilities varied slightly.

This is a small transport probe, not a cold end-to-end production benchmark or proof of
classification accuracy. It supports removing per-judgment process/connection setup.
It does not justify multiplying these timings into a promised daily finish time; long
pack inputs, failures, serialization and scheduling remain to be tested. The running
1,000-record semantic experiment was not switched mid-run. Receipt IDs:
`b998bf2e660f599602deedf9042cb9eb39d0934035a5a59b7a9d2e4c1fbb20f0`,
`282d92cbec2de6e83dccff23f60854319dcab127c1bbf9087755934fa77b4713`,
`b320840b7ba52d5653f87e3594ef31944fa47a1c83d12eb6ec6f17662a1684ba`,
`b25704100e6c093861a407b4440edee39b9ac15bc2a3a8ab8a29585012599458`.

Additional acceptance gates: reconcile captured parent context before avoidable uncertain
retention; lossless handling of overlong originals/pairs; candidate-recall audit on missed
same-direction material; independent review of exclusions and suspected wrong merges;
same-material Phase 3 report comparison; production credential/version policy and installed
snapshot smoke. The experiment-only Gateway key expires September25. None of these can be
replaced by passing unit tests or a larger message-count headline.

### Concrete implementation plan after this bounded study

1. Supply evidence before changing classifiers: attach already-captured missing parents by
   explicit IDs, preserving provenance and original versions. Keep current text, quotations,
   parent context and unresolved media distinct. Never invent linked content. Freeze the
   reading-view/prompt identity; old tasks resume with their old input contract.
2. Keep the global index as candidate discovery only. Page retrieved candidates through
   Jev under explicit input limits instead of dropping all but four or omitting an entire
   candidate due to a crowded request. Measure candidate misses separately from model
   membership errors. No predetermined cluster count or keyword-equality grouping.
3. Jev remains responsible for information presence and reading-pack membership. An
   unresolved record is retained; a candidate pack is not evidence of its missing parent.
   Carry screening uncertainty into membership and require actual supplied context to
   resolve it. Where representative selection needs improvement, choose original-record
   IDs rather than generating topic summaries or research questions. Do not tune additional
   thresholds against the just-reviewed1,000 cases and call that holdout acceptance.
4. Replace per-call startup with a long-lived SDK client, cache completed judgments, and
   parallelize independent requests within the existing experiment budget. Pack-state
   changes need versioned commits/revalidation so concurrency does not introduce duplicate
   or silently overwritten assignments. No separate agent tier or memory controller.
5. Validate the same materials with complete cost accounting, then a new untouched source-
   stratified holdout, explicit source review of exclusions/disagreements, input/order/resume
   tests and a Phase3 same-material report check. Match the retained source universe when
   comparing to Codex; do not substitute package-count gains for research quality.

This is a proposed productionization plan, not a claim those remaining steps have shipped.
The experiment is complete; Jev replacement acceptance and production deployment are not.
Keep the installed baseline and the15-thread /6-concurrent /20-packs-per-task Phase3
boundaries. No Wiki, delivery-recipient, schedule or historical-report migration belongs
to this change. Production API spending and a durable credential must be established before
enabling a new recurring paid-provider path; no further purchase or automatic reload was made.

## Reproduction and safety

Experiment root:
`~/Library/Application Support/ai-digest/validation/jev-20260918/scale/`
Final path: `content-v2-eligible-qlocal8-contentindex-bounded-reading-records/n{size}`.
Receipts contain source hashes, per-call IDs, judgments, usage and original coverage.

```sh
JEV_HTTPS_PROXY=http://127.0.0.1:6152 .venv/bin/python scripts/experiment_jev_scale.py \
  --source '/Users/zoharhuang/Library/Application Support/ai-digest/runs/2026-09-08/attempt-0001' \
  --root '/Users/zoharhuang/Library/Application Support/ai-digest/validation/jev-20260918/scale' \
  --budget-root '/Users/zoharhuang/Library/Application Support/ai-digest/validation/jev-20260918' \
  --size 500 --variant content-v2 --pair-batch 8 --workers 4 \
  --eligibility-guard --question-local --clean-retrieval --bounded-lexical \
  --relation-policy reading --unit-mode records
```

Run large cases sequentially. Earlier simultaneous variants hit ENOSPC while writing
aggregate snapshots; they are failed experiments, not completed benchmarks. No unrelated
files were deleted. Reduced embedding batch size, durable per-call caching, phase-end
aggregate writes and a 5 GiB pre-call free-space guard allowed subsequent runs to finish.
Eight historical requests remain reserved without a returned result; no blind duplicate
retry was issued. One fully returned, validated but unsettled result was recovered and
charged once in the local ledger. Cached successful requests are not billed again locally.

Engineering checks: 328 tests passed, targeted Ruff and mypy passed before this report.
Those checks do not establish semantic acceptance. No OpenAI inference calls, GPT-6
dispatches, live publishing or production task changes occurred in these experiments.
