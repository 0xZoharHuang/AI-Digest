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
scale tests. The 134/1000 real tiers remain unexecuted. Offline 2000-package
lifecycle and publication tests have passed. Subsequent results are recorded below.

Production remains app-97314ecb20c1-20260920T084156Z. No reading-mode activation
or new-mode Wiki/DM was performed. A question is pending whether the user permits
a same-model gpt-5.6-sol **high** comparison; all research pilots so far used the
approved **medium** profile. Do not silently change production reasoning or reuse
one profile's scale acceptance for another. Future scale receipts bind code,
model, reasoning, reader profile, exact outputs and a fixed semantic review sample.

## September 21 checkpoint

R7 67-package review permitted proceeding to 100, not production acceptance.
The 100-package R7 run completed in 1787.95 seconds with one thread and one
same-thread artifact repair: 41 skip, 33 insufficient, 14 brief, 12 report-linked
packages supporting three reports. It failed semantic review: a cached StreamLib
page's delivery guarantees were presented as current, conflicting with the raw
README. Keep this failed evidence; do not promote to 134/1000 yet.

The app-74a6a10aa783-20260920T154445Z installed/background engineering exercise
passed in 744.67 seconds: real Jev, one real 5.6 research thread, Brief, isolated
queue recovery and local-only Wiki/DM generation. UID 501, parent PID 1, Node
v25.2.0; completed replay unchanged, Phase 2 completed results reload correctly.
Its engineering_preflight.json is explicitly NOT a release acceptance receipt.
No live Lark writes occurred. Read-only server authentication verification passed.
Full automated suite: 386 passed; Ruff and mypy (59 source files) passed.

The next correction makes raw/versioned retrieval explicit for mutable API and
safety guarantees, source IDs literal manifest references, and experiments separately
attributed. This changes instructions and invalidates prior release evidence for
the new candidate. Production still uses the old verified snapshot.

R8 100-package run completed in 1587.76 seconds, one medium thread, one compaction,
no artifact-repair invocation: 35 skip, 31 insufficient, 24 brief and 10 report-linked
packages forming two reports. It still failed semantic review on StreamLib's current
delivery profiles and writable MCP surface. Do not expand to 134/1000 yet.

Snapshot app-b77a16fb7fd6-20260920T161431Z passed its own installed/background
engineering exercise in 960.66 seconds. All 388 automated tests passed. Its
engineering receipt is not release approval. Semantic approval now binds actual
report/short-update/evidence bytes, with a deployment-time recheck.

The research terminal's network is disabled, independently reproduced with a
matching sandbox probe. The user approved Phase 3-only proxy-mediated public
reading with local/private access still forbidden. However, the Mac resolves
public research domains to 198.18.* synthetic addresses; Codex's private-network
guard rejects them even with the upstream proxy. Do not disable that guard or TLS
verification. Narrow DNS-rule adjustment approval is pending. No production or
running-task permission change has been applied. This capability gap is observed;
it has not been proven to be the sole cause of semantic errors.
