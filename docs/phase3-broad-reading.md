# Broad reading release candidate

The reader's goal is understanding: what an object is, how it works, what it adds,
and why it matters. Evidence checking supports that goal; it is not the report's
default subject. A package is a reading responsibility, not a mandatory article.
Inputs are signals, not prescribed research objects or boundaries. The researcher
may investigate an underlying question beyond the seed project, using other
papers, implementations, official material and community evidence. Two or three
dozen minutes of real work is normal, not a minimum runtime or a reason to stop.

`phase3_reading_target` defaults to zero while this release is unaccepted. Once
accepted, new jobs use 1000, with separately tested tiers of 1500 and 2000. There
are at most 15 research threads, 6 simultaneous workers, and 134 packages per
thread. The existing 5.6 model remains unchanged. The old 20-package contract
continues only for frozen legacy work. Phase 2 is not changed.

## Data path

Source-stratified priority/exploration selection -> byte-balanced tasks ->
complete paged originals -> per-package reading decisions -> independent report
catalog and short updates -> Brief -> existing daily Wiki hierarchy.

The reading contract is `autonomous-reading-v1`. A package decision is skip,
brief, insufficient or report. Missing/failed outputs remain failures, never
semantic exclusions. Several same-task packages may share one independently
identified report. The package population remains unchanged. Old consumers use
a validated publication projection only after exact raw-package outcome coverage
is checked; reports must link exactly to their original members.

The file reader emits at most 24000 characters per page. Sources and unknown
semantic payload fields, engagement, version/change information and captured
context remain available. A receipt proves delivery, not cognition. Agent-visible
progress is a copy of the owner-side accepted checkpoint; modifying it cannot
invent completion. Accepted inputs and reports are hash-protected on recovery.
Same-thread recovery and native Codex compaction remain in use.

Brief notes go to one daily “其他发现” page. Skip decisions are internal. Deep
reports keep detailed evidence ledgers; short decisions do not require the agent
to rewrite redundant intake and publication manifests. Compatibility ledgers are
compiled by code and explicitly labeled as reading dispositions, not independent
fact verification.

## Required acceptance (not yet claimed)

- 67/100/134-package real pilots: exact coverage, no serious false dismissal,
  credible source use and reader-first reports. Review all dismissals and gaps.
- 2000-package offline lifecycle and import/publication tests.
- 15-task/1000-package real isolated run using existing Phase 2 data, review all
  reports/gaps plus a frozen 200-result sample; record full usage and elapsed time.
- Installed/background E2E, no real validation push, immutable completed replay.
- Empty production queues and idle services before switching, with rollback kept.

Use `scripts/evaluate_phase3_reading.py` for isolated pilots. Its output says
`executed_not_semantically_accepted`; execution never grants semantic acceptance.
The three known report cases are held constant and the rest selected by a fixed
seed. Preserve failed pilots and original threads; do not replace a task to erase
its failure. Do not activate the candidate merely because the old tests pass.

## Development checkpoint, September 20

The implemented contract adds an explicit researcher mission, literal source
identity anchors and per-page working decisions (`pending` is never completion).
Comparisons and current API claims must use version-aware primary evidence rather
than search-index text alone. Published reports and short updates are separate
from the raw-package disposition ledger; Brief gets a dedicated navigation contract.

Six real 67-package pilots completed in isolated directories. R6 processed all
67 in 1177.44 seconds: 24 skip, 20 brief, 17 insufficient and 6 report-linked
packages supporting 3 independent reports, with no execution failures. Nevertheless,
semantic acceptance was NOT granted: the control-plane report asserts that a
traditional README cannot be versioned, distributed with an image, or statically
reviewed, an unsupported comparison. Earlier identity and missing-context problems
improved, but factual precision is not yet stable enough to justify scale rollout.

The R1-R6 review records and outputs remain under the local runtime's
`validation/reading67-20260920-r*` directories. Do not describe them as passed
scale tests. The 100/134/1000 real tiers and installed new-contract E2E remain
unexecuted. Offline 2000-package lifecycle and publication tests have passed.

Production remains app-97314ecb20c1-20260920T084156Z. No reading-mode activation
or new-mode Wiki/DM was performed. A question is pending whether the user permits
a same-model gpt-5.6-sol **high** comparison; all research pilots so far used the
approved **medium** profile. Do not silently change production reasoning or reuse
one profile's scale acceptance for another. Future scale receipts bind code,
model, reasoning, reader profile, exact outputs and a fixed semantic review sample.
