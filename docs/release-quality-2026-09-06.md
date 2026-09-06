# Phase 2 quality follow-up — local production release

Deployed on September 6, 2026, around 18:36 Asia/Shanghai.
Code revision: `08f1bcd5770a`.
Immutable snapshot: `app-08f1bcd5770a-20260906T103100Z`.
Rollback target: `app-4a78bf219082-20260905T182711Z`.

## Production behavior

- Phase 2 preserves the complete original-input and label ledger. Empty deletion
  tombstones do not consume candidate packages; uncertain readable signals remain.
- Bulk annotation and bounded primary-object matching use **Luna / medium**.
  Local Qwen3 embeddings propose neighbors, never authorize a merge. Short-name
  normalization uses **Sol / high** only for shared ambiguities in bounded scopes;
  selective primary-object ambiguity checks use Luna. No full-corpus strong-model
  reader or growing all-corpus conversation is introduced.
- There is no fixed target number of packages and no Phase 3 budget input to Phase 2.
  Exact repository/paper identities, original evidence and explicit uncertainty are
  preserved. Excess comparison capacity is reported rather than silently dropping data.
- Phase 3 remains one independent research lead per selected package, at most **15**
  daily: normally 12 priority plus up to three source-balanced long-tail selections.
  Shortlisting is Sol/high, research is Sol/medium, concurrency is three. Unselected
  packages remain stored. Deterministic sampling and selection replay are recorded.
- Brief generation remains **Terra / high**. Code, not the model, counts observations,
  normalized information, packages, selected coverage and recorded reviewed coverage.
  Historical artifact formats are also supported. Missing package references cannot
  produce a misleading or negative count. Required report links are checked again
  after formatting before a completion marker is written.
- Article failures retain metadata, error categories and bounded persistent retry
  state. Source partial/failure status remains visible. Existing Lark publishing and
  notifications continue; no replacement channel or new account was configured.

## Acceptance evidence

| Check | Result |
|---|---|
| Final source regression | 252 passed; 86.86% measured core coverage; Ruff and mypy passed |
| Frozen corpus | 6,878 normalized units, 5,813 natural packages |
| Information retention | 193/195, 98.97% |
| Same-object pairs | 110/113, 97.35%; no aggregate regression vs accepted baseline |
| Different-object pairs | 118/118, 100% |
| Explicit boundary challenges | Passed |
| Aggregate recorded model tokens | 17,220,479 input + 624,420 output; +9.01% vs same-input baseline |
| September 6 replay | 3,217 units, 2,793 candidate units, 2,493 packages; 319 empty tombstones excluded from candidates |
| Daily full-catalog admission | 15 selected, including three exploration; replay identical; Phase 2 hashes unchanged |
| Large-package review | All five packages with at least ten members reviewed: 217 members, four uncertain conversations retained |
| Source-tree real smoke | Six reports, zero research failures, queue traversal and publishing preflight passed |
| Installed real smoke | Three reports, zero failures, six in-memory Wiki pages, no unresolved internal links |
| Final installed package | All four frozen Phase 2 artifact hashes reproduced with zero new model calls; semantic gate passed again |
| Background runtime | Installed Codex and Lark authentication verified, owner/recipient match checked |
| Cutover | All three LaunchAgents loaded from the final snapshot; consumer initial exits zero; active queues empty at check |

The real installed research smoke ran on `e0c6c2e1681f`. The final release differs
only in deterministic `run_counts.py` compatibility handling. Installed Python-file
hash comparison confirmed that boundary; counts/text are identical for new-format
daily and smoke artifacts. Final installed publishing verification and full Phase 2
cache-only reconstruction passed without repeating paid research.

The token guard concerns recorded input-plus-output tokens, **not a dollar invoice**:
input increased 8.45%, output 27.06%. Cached counters and 73-second cache-only replays
are not cold-runtime or million-record daily throughput claims.

## Quality limits and preserved evidence

The frozen reference was not changed to pass this release. Three missed same-object
pairs remain counted as errors. References are model-assisted with original-evidence
review, not human-certified gold or a guarantee of zero semantic error.

The separate 60-pair fragmentation diagnostic retains its original score: 9/10 same,
26/26 different. Blind discrepancy/control review and direct original-payload checks
identify three uncertain relations; the reviewed score is 9/9 same, 24/24 different,
27 uncertain. All 20 adjudicated decisions, including rejected reviewer claims, are
recorded with source and output hashes. See the [quality report](phase2-quality-followup-2026-09-06.md).

The daily candidate remains mostly singleton packages: 2,427 of 2,493. This release
does not claim artificial compression of unrelated material. The largest package has
174 units; the known Muse evaluation is correctly separate in a four-unit Muse package,
and an unrelated rhetoric post is no longer an Astra member.

HTTP 403/404 and unavailable article bodies remain external source limitations, visibly
reported rather than presented as recovered. Today's existing published report was not
rewritten, and no extra 15-agent daily research run was launched during validation.

## Operations and recovery

The one-shot background probe exposed a parsing issue: the npm Lark wrapper can print
its native-binary installation notice before the first successful auth JSON response.
The probe now extracts and validates the auth-status object; the production publisher
already tolerates CLI preambles. Final-snapshot background verification passed. No
credentials, account identity, proxy or permissions were changed to make it pass.

The producer's normal startup catch-up may collect incremental sources/papers after
cutover; it does not imply a second daily research budget. Consumers remain scheduled
and loaded while idle. Existing notifications continue on the normal production path;
deployment validation sent no live messages or Wiki writes.

One oldest immutable program snapshot (`app-a6f96dbfd258-20260901T112413Z`) was removed
by the existing three-snapshot retention policy. It can be rebuilt from its source
revision; runtime data, reports, evaluation evidence and database backups were not
deleted. The pre-release `e0c6c2e1681f` snapshot was moved recoverably into `upgrade-hold`.
The former active `4a78bf219082` snapshot remains the direct rollback target; the older
`f2c555ea20c0` snapshot is also retained.

Private deployment receipts are under the runtime's `deployments/20260906-quality/`.
Source commits are local; no remote push or cloud deployment was performed.
