# Phase 2 fixed-input release — 2026-09-20

## Selected design

Phase 1 freezes the original-preserving reading handoff, including available local
quotes, parents and full-text blobs. Phase 2 never fetches missing context.

```
N original IDs -> frozen hybrid-index draft -> fixed reading pages
                                       Jev signal + membership questions
                                          /                 \
                              proposed chatter       retained / uncertain
                                independent review       fits / exception
                                     |                       |
                                     R        one frozen candidate correction pass
                                                             |
                                               approved folder or singleton
                                     \_______________________/
                                      exact disjoint partition N = R + members
```

The graph proposes reading neighbours, not final merges. Every original is an
explicit Jev target; long originals are losslessly paginated. Same-product uses,
versions and disagreements, or projects within a concrete technical direction,
may share a folder. No Phase 3 budget influences these decisions.

Production uses fused independent questions (`jev_reading_v3`) in packaged Python
and a packaged Node worker. The split question layout is evaluation-only. Old
completed contracts remain readable. An incomplete old-contract job is not
silently upgraded; retain its matching snapshot when recovering it.

## Evidence obtained before installation

Frozen artifacts live outside Git under the local runtime `validation/` directory.
No real publication was made by these experiments.

| Same-material experiment | Fused | Split |
|---|---:|---:|
| 50 real originals: final packages / exclusions | 47 / 0 | 47 / 0 |
| Successful logical requests / questions | 181 / 281 | 189 / 277 |
| Input tokens | 502,310 | 551,199 |
| Output tokens | 11,815 | 11,673 |
| Provider returned actual cost (USD) | 0 | 0 |
| Provider returned market cost (USD) | 0.021097020 | 0.023150358 |
| 14 explicit boundary cases: checks passed | 6 / 6 | 6 / 6 |
| Boundary input / output tokens | 21,759 / 1,759 | 24,257 / 1,726 |
| Boundary market cost (USD) | 0.000913878 | 0.001018794 |

Evidence directories: `fixed-20260920-small-r1` and
`fixed-20260920-regressions-r1`. Both layouts agreed on final membership,
exclusions and per-original classifications. One prompt correction was needed:
self-reference must not prove membership. The earlier mistaken Gaussian-math /
PAPT merge disappeared. Inspection included every final multi-item folder and
the explicit boundary sources; this is not independent human ground truth.

Fused saved 8.87% of real-sample input tokens but used 142 more output tokens.
The choice is fewer duplicated inputs/requests and equal observed judgments,
not a claim that every cost dimension improved. Provider actual zero is a receipt,
not a promise of free service. Ambiguous failed attempts have unknown billing;
market cost is not an invoice. Cached/resumed durations are not latency estimates.
The first failed candidate and failed requests remain in their journals and are
not included in this successful-logical comparison table.

Offline index inspection on those same 50 records found six final co-package
pairs: five appeared in dense candidates but only two in lexical candidates,
with zero explicit-ID edges. This is a small post-hoc recall proxy, not independent
truth, but does not support replacing the existing dense channel with lexical-only
retrieval in this release.

Mock scheduling/partition/replay passed at 6,000 and 60,000 originals, without
paid model calls. Observed maximum RSS was approximately 97 MB and 480 MB, elapsed
1.98 s and 20.56 s. These do not measure full embedding-index construction or
large-corpus semantic quality.

## Production hardening

- Original-ID accounting, chatter exclusion, assignments and catalog use one
  partition. Failure-notification placeholders are never semantic completion.
- Completed Jev results use their own validator, not the old labels loader.
- Input snapshot, draft, prompt, request and final artifacts have explicit hashes.
  Changed completed evidence fails closed; successful requests resume from cache.
- Network/authentication/quota/parse failures never become `unclear` or `chatter`.
  Returned transient failures receive bounded retries; ambiguous EOFs require
  explicit resume and preserve uncertain billing.
- Worker code is an installed package resource. No experimental script entrypoint,
  repository-parent path assumption, Node `--use-env-proxy`, or Unicode-line split.
- Gateway credentials use the dedicated production Keychain service. No daily
  local cost cap and no automatic recharge. Provider quota/auth failures remain
  visible rather than falling back to a stronger model.
- Cutover of the new contract requires a matching installed end-to-end acceptance
  receipt, empty queues and idle services. Configuration edits invalidate receipt.

## Installed acceptance procedure

1. Commit tested source; `scripts/install_macos.sh --apply` stages an immutable
   non-editable snapshot without changing the active services.
2. Run `scripts/accept_fixed_release.py --snapshot PATH --smoke-root ISOLATED_PATH`
   with that snapshot's Python, in the same background user environment.
   It uses one 5.6-sol research task, the existing 5.6-terra brief, isolated queues,
   real Jev, strict result reload, completed queue replay, publication preflight
   and local Wiki replacement. It sends no real Wiki or direct messages.
3. Inspect artifacts and the receipt; only a passing snapshot may `--cutover`.
   Preserve the previous snapshot for rollback. The next natural run is additional
   evidence, never a substitute for this acceptance.

## Known limits

The real sample remained mostly singletons: this does not establish increased
information density or global optimality. Missing context is conservatively kept,
including empty tombstones in the current version. Large-group references are
bounded excerpts, not a proof that every pair in a folder belongs together.
Candidate recall is bounded by the current hybrid index; it was deliberately not
replaced in this release. Exceptions may need many candidate comparisons; the
50-source run demonstrates that they can dominate request volume. Full-text-only
retrieval versus dense retrieval still needs a separate, attributable offline
study; it is not an acceptance claim of this release.

Release state is determined by `release_acceptance.json` in the installed snapshot
and the loaded LaunchAgent paths, not by this document or a successful Git push.
