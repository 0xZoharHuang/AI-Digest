# Semantic labels validation

Status: Phase 2 semantic release gates passed on the frozen corpus after evidence adjudication.
Local production cutover to `4a78bf219082` completed on 2026-09-06; see
[the deployment record](release-2026-09-06.md) for installed/background and live-publishing evidence.

## Release candidate evidence

The final candidate retains all 6,878 normalized units (7,578 source observations), with 5,998 present,
777 unclear and 103 confirmed chatter labels, and 6,130 natural packages. No Phase 3 quota was used to
set package count. The model-assisted reference after evidence review measures retention 193/195
(98.97%), same-object recall 110/113 (97.35%), and separation 118/118 (100%). All explicit lost-paper
and unrelated-object regressions pass. These are sample point estimates, not guarantees; Wilson
intervals and the three remaining false-separated pairs remain in the acceptance receipt.

The unadjudicated new reference scored 110/120 (91.67%). Blind re-review of the ten disagreements,
mixed with twenty unchanged controls, identified seven unsupported claims that February training
rollback and April RL-stack changes came from the same disclosure. Their captured shortlinks are
unexpanded and the supposed common source is absent. The primary agent checked the original evidence:
these seven pairs are uncertain, not relabelled as definitely different. The other three misses remain
counted against the implementation. Original drafts, quotes, adjudication and provenance hashes are
preserved. This is not human-certified gold and does not claim zero semantic error.

A large-package audit found a bare-link observation acting as a bridge between Atlas and Muse Voice
in different comparison contexts. Unanchored observations can now attach to one established identity
but cannot unite multiple identities through conflicting contextual guesses. Explicit distinct
multi-member identities are also respected. The cache-only full replay separated these products
without reducing either pair-reference score or executing a new model call. Final captions prefer
captured original titles; unresolved bare links do not receive an invented object name.

The accepted full computation records 510 logical calls: 225 annotation, 19 negative confirmation,
266 identity comparison. Recorded usage is 15,879,129 input tokens (3,301,120 cached) and 491,423 output
tokens. These are model-token counters, not a Codex subscription invoice. Successful calls were
reused across interrupted experiments; a roughly 21-second cache-only replay is NOT cold runtime.
The final continuation at concurrency 8 took 364.71 seconds for 199 newly executed comparison calls;
earlier stages and local embedding work are additional. Production retains its explicitly configured
concurrency until separately changed. Synthetic million-vector capacity evidence below does not
establish a million-record daily pipeline SLA.

## Development history

Installed snapshot `7194de6` passed doctor with the actual Luna/medium, Sol/high, Sol/medium and
Terra/high profiles and a real isolated smoke in 595.66 seconds (3 reports, zero failures, no live
Lark writes). A subsequent full-catalog admission test correctly blocked release of that snapshot:
its 1,990,503-character prompt exceeded the 1,048,576-character input limit. Bounded Phase 3
shortlisting then consumed all 6,130 cards and selected 15 packages in 7 calls across 2 levels,
using 559,633 input and 5,412 output tokens. All Phase 2 artifacts remained unchanged. The newer
selector must be included in the final installed snapshot; the earlier snapshot is not cut over.

The first complete 6,878-unit run finished structurally but failed semantic acceptance: 6,342
packages, information retention 193/195 (98.97%) against the independent reference, correct separation
121/121, but same-object pair recall only 49/117 (41.88%). Of 68 missed pairs, 62 were split across
execution scopes. This disproves using disjoint execution scopes as final classification boundaries.
The replacement covers candidate relations with overlapping identity-card scopes and combines only
model-confirmed equivalence relations across scopes. Its full semantic recheck remains in progress;
offline candidate co-location coverage of 115/117 is not a semantic pass.

The overlapping-scopes full run completed with 6,119 packages. Reference same-object recall rose to
100/117 (85.47%), while different-object separation remained 121/121. This still fails the 95% recall
gate. Sixteen of the seventeen missed pairs actually shared a comparison call; candidate coverage
alone was not the remaining cause. A complete per-card identity-assignment probe is being compared
with sparse merge output before spending on another full run. The negative-confirmation run was
interrupted with its valid checkpoints intact because it retained this failing merge contract.

An isolated real Phase 1–5 integration smoke passed in 671.04 seconds: 7 original items, 6 units and
6 packages, 3 independent selected leads, 3 reports, no failures, and 3 unconsumed packages retained.
Queue traversal was jobs -> completed -> archived. Wiki dry-run produced 6 nonempty pages and no
unresolved internal links, with zero live Lark calls. This validates integration, not full Phase 2
semantic quality or real publishing/notification transport; it predates negative-confirmation changes.

The 1,000,000-vector synthetic index run completed in 2,067.75 seconds at 4,199 MiB peak RSS, using
four native threads and 1,024 dimensions. Self-retrieval@1 was 0.73 at that scale with ef=64, so those
parameters are not accepted for million-scale retrieval quality. This is capacity evidence only.

Current candidate: `gpt-5.6-luna`, `medium`, text-only tool profile; 32 records / 128 KiB per
annotation call. Input aliases follow record order; output contains only signal, kind and short
group name. Cross-batch review returns only disjoint group-ID sets to merge, with all other groups
retained. Missing captured bodies abstain instead of becoming chatter.

A subsequent census of all 149 initially discarded records found four complete paper abstracts and
the Semantic Overlays project announcement incorrectly marked as chatter. The 200-record reference
had not sampled these errors; its aggregate retention score was insufficient evidence to release.
Production now requires an independent small-context confirmation only for proposed chatter (at most
8 records / 64 KiB per call, same Luna/medium model, original predictions hidden). Either reading
retaining information prevents exclusion. No strong-model pass over the entire corpus was added.
The 149-record check recovered 46 candidates, including all five concrete failures. Its 19 calls
used 351,415 input tokens (49,920 cached) and 9,058 output tokens. Some recovered items are genuinely
ambiguous; retention is not an assertion of research value. A fresh full packaging run with these
corrections is required. Cached first-pass labels and embeddings remain reusable.

The first-pass multi-member-group census also exposed unrelated records sharing a provisional name
(for example an indirect prompt-injection paper and a retail-banking prediction paper). The final
candidate therefore indexes each retained unit separately; temporary names never form indivisible
packages. Global nearest-neighbour candidates no longer exclude same-batch records. Complete per-card
representative assignments recovered 11 of 16 co-located failures in a seven-block development probe
without sampled false joins; that targeted probe is not acceptance. Exact primary URL plus original
title identifies literal duplicate article observations, independently of model naming. Cards now
include original text previews, abstracts and quoted context as available, not only a headline.
The revised full run and a newly sampled pair reference remain pending. Known lost-paper and
unrelated-pair cases are explicit development regressions in `eval/phase2_label_boundary_regressions.json`.

A compact integer-array identity-output probe reduced seven-block output from 26,908 to 15,530 tokens
and median call latency from 80.04 to 46.48 seconds. It recovered 12/16 targeted missed pairs rather
than 11/16, but introduced an incorrect join between the record-label lawsuit and a separate Claude
security incident. It is not promoted to production. The per-ID assignment contract remains in use;
these development probes do not establish global optimality or holdout accuracy.

The new pair reference excludes all 240 earlier pairs and contains 120 same-object, 118 different-object,
and 2 uncertain pairs. Its original inputs and output receipts are checked against the frozen corpus,
not merely matched by familiar IDs. It remains a model-assisted draft until discrepancy review.

Current code regression: 211 tests pass, 85.73% measured coverage. Smoke configuration round-trips
all Codex settings, including the Phase 2 engine/model/reasoning/tool profile, without default drift.

The acceptance task is information-signal retention and natural research package boundaries,
not reproduction of historical Research/Watch/Archive decisions. Development labels must distinguish
clear chatter from incomplete but potentially meaningful signals. Pair examples must distinguish the
same event/object/question from merely similar fields or companies.

Run an isolated sample (no Phase 3, publisher, or source mutation):

```sh
uv run --extra semantic python scripts/run_phase2_labels_validation.py \
  --source /absolute/path/to/sealed-run --target /absolute/path/to/new-validation \
  --sample-per-source 2 --reasoning medium --text-only
```

Omit sampling for the full corpus. A different reasoning setting must use a different target. Outputs
include full normalized input, labels, all candidate packages, file hashes and per-call usage.
`attempt-*.json` retains invalid model output and its validation error, as well as valid attempts.
Replaying an already sealed target verifies its input hash and every output before returning.

Observed during implementation on 2026-09-05:

- 198 automated tests passed. These verify contracts and regressions, not semantic acceptance.
- The local embedding probe loaded the pinned model and retrieved two paraphrases as neighbours.
- Initial file-reading Luna/low sample: 22 units, 231,741 input tokens, 2,245 output tokens.
- Same sample using stdin: 22 units, 22 packages, 21 present/1 unclear; two calls including a repair,
  81,129 input and 2,652 output tokens. Different chatter judgments require semantic review.
- The 60-unit keyed-label/name-only contract passed in one call: 59 packages, 72,358 input tokens,
  2,213 output tokens, 50.97 seconds. Earlier array outputs duplicated or invented IDs; making each
  input ID a required object key removes that structural failure mode. Short names directly identify
  groups, avoiding a second inconsistent group-name lookup table.
- Regression coverage after adding candidate-index, cache, import-corruption and pipe-input tests:
  203 tests passed, 86.17% coverage. Neural embedding inference is mocked in unit tests; its actual
  loading/paraphrase probe is separate evidence, not a clustering-quality benchmark.

Outstanding release gates: held-out signal/pair evaluation, full 6,878-unit result and package review,
reasoning comparison, measured end-to-end cost, bounded-memory scale checks, three distinct Phase 3
research smoke packages, and snapshot/notification/publishing verification. Do not treat a structurally
valid sample, a model loading successfully, or a queue completion marker as these acceptance results.

The first full trial also measures whether cross-batch comparisons cost too much. Candidate search
reduces comparison scope but does not guarantee a low model call count. Its threshold is a development
parameter pending held-out evaluation; no million-record throughput or cost guarantee has been made.

Additional evidence from this development turn:

- The old long-ID 60-record trial incorrectly joined a trust-model paper to Delta-AI. Ordered local
  aliases and smaller batches separated the two correctly. A passing JSON schema alone did not
  catch the semantic mistake.
- Reviewing one candidate group per call required 10 calls for 60 records without making any
  merges. Batching these comparisons reduced this to three calls; annotation checkpoints were reused.
- Six known cross-source development cohorts now coalesce correctly, including the joint model
  launch, system card and independent evaluation. Asking for merge-ID sets worked better than
  asking the model to rename every candidate group.
- A separate 200-record reference set excluded the earlier samples. Against its unadjudicated Sol
  reference, low/default-tools missed six retained-information cases; medium/text-only missed one,
  an empty captured body. This is model-assisted reference agreement, not human-certified truth.
- A separate pair reviewer labels 240 proposed pairs without seeing the old group labels or the
  new model's predictions. The second proposal set includes similar-name cross-object pairs and
  limits repeated anchors. References remain drafts until discrepancy review.
- `benchmark_semantic_index.py` measures synthetic 1,024-dimensional index capacity only. Its
  throughput is not the full pipeline's throughput, and its self-retrieval check is not semantic recall.
