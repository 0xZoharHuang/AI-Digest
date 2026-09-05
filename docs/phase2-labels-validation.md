# Semantic labels validation

Status: implementation and experiments in progress. Not approved for production cutover.

Current candidate: `gpt-5.6-luna`, `medium`, text-only tool profile; 32 records / 128 KiB per
annotation call. Input aliases follow record order; output contains only signal, kind and short
group name. Cross-batch review returns only disjoint group-ID sets to merge, with all other groups
retained. Missing captured bodies abstain instead of becoming chatter.

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
