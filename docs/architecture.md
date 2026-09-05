# Architecture and data contracts

## Reader-first pipeline

```text
platform observations -> sealed source JSONL -> exact observation units
  -> bounded Phase 2 Readers -> precision Readers -> one object Editor
  -> selected research objects
  -> package research leads -> Chinese main reports/optional subreports
  -> navigation brief -> private Lark Wiki + self-DM
```

Phase 1 maximizes durable observation coverage. Phase 2 understands and groups without browsing,
importance ranking or research. Phase 3 follows new information into primary artifacts; it does not have to invent
a unified thesis. Phase 4 is a reading index, not a second research or decision layer.

## Runtime truth

The default runtime root is `~/Library/Application Support/ai-digest`:

```text
store/
  blobs/<sha256>
  fetches/<source>/<date>/<fetch-id>/manifest.json
  revisions/<source>/<item-id>/<revision>.json
runs/<date>/attempt-0001/
  00_run_manifest.json
  01_phase1/{x_list,x_for_you,github,papers,articles,hackernews}.jsonl
  02_routing/{units.jsonl,decisions.jsonl,objects.json,phase2_manifest.json}
  03_research/<object-id>/{main_report.md,intake.jsonl,evidence.jsonl,subreports/,research_manifest.json}
  04_brief/daily_brief.md
  05_publish/publish_manifest.json
state.db
queue/{staging,jobs,retry_wait,completed,publish_pending,archived,failed}/
```

Files remain the canonical evidence and agent handoff. SQLite contains rebuildable cursor, revision,
ready/delivery, snapshot and run indexes.

## Observation time and delivery

Every `SourceItem` separates:

- `occurred_at`: source publication/event time;
- `updated_at`: source revision time;
- `first_observed_at`: first local observation;
- `ready_at`: time the durable observation became eligible for delivery;
- `observation_kind`: `live_increment`, `late_arrival`, `content_revision`, or
  `bootstrap_snapshot`.

A daily seal drains every undelivered non-bootstrap observation with `ready_at` before the cutoff.
There is intentionally no lower time bound: a late observation after an outage cannot become
permanently stranded. Initial historical article listings are retained as bootstrap context and do
not masquerade as today's news.

## Source contracts

- X List is a complete configured-list increment using `sinceTime`, pagination and overlap. Cursor
  advancement follows durable raw/page receipts. X For You is explicitly a sampled recommendation
  surface.
- GitHub is bounded discovery over configured queries, Trending and tracked repositories. It emits
  lane entry, star crossing/growth, release and material metadata events; it is not a claim of global
  GitHub completeness.
- arXiv consumes the complete configured multi-category daily feed while the machine is online. A
  submitted-date API query is a bounded offline supplement, not an exact replay of missed announce,
  replace, withdraw or cross-list events. Hugging Face Daily Papers is a dated, paginated surface
  with bounded missed-date replay. They remain separate observations until unitization.
- Hacker News scans item IDs from the durable `maxitem` cursor, advancing a bounded backlog chunk
  only after the whole chunk is durable. New/top/show are attention surfaces on one story identity,
  not three story records.
- Media adapters retain raw discovery/body responses, normalize canonical URLs, recurse sitemap
  indexes and emit content-hash revisions. Extraction failures preserve metadata for Phase 3.

`SourceHealth` records operational status separately from `coverage_mode`:
`complete_increment`, `bounded_discovery`, or `sampled_surface`. Receipts include pagination/cursor
state, duplicate/revision counts, observed time range, raw completeness and quiet rationale.

## Phase 2 contract

Deterministic unitization groups only provably identical entities: an X post/conversation, GitHub
repo, arXiv paper, HN story or canonical article. Semantic similarity never deletes evidence.

Phase 2 gives every complete normalized unit to a bounded semantic Reader. Batches contain at most
96 units and 256 KiB, interleave sources, and run with a small fixed concurrency. A Reader may use
scripts for navigation and coverage checks, but only Codex may assign `research`, `watch`, or
`archive`; Archive must be a positive semantic decision rather than a default for records outside a
keyword or score threshold.

```json
{
  "unit_id": "u_...",
  "route": "research",
  "object_key": "concrete-object-hint",
  "object_label_zh": "具体对象",
  "reason_zh": "一句选择依据"
}
```

Each batch response is constrained by JSON Schema and then checked for exact, unique unit coverage.
An invalid batch is returned to its own saved Codex session with the concrete missing/duplicate IDs;
valid batches are immutable recovery points. Reader batches may run concurrently. The first pass is
deliberately high-recall; a second independent bounded pass of at most 64 units and 192 KiB re-reads
every unit. Archive requires agreement from both readers; any disagreement retains the unit as Watch
or Research for final review. Both passes remain flat, rebuildable files rather than an opaque
workflow state machine.

After both passes are valid, one higher-capability Editor receives Research object candidates with
their full normalized units and a mechanical `source_signals` view of event/release/growth/engagement
fields. It decides which objects independently merit Research without seeing downstream cost,
concurrency, model, or daily capacity; it reviews a
deterministic per-source Archive sample plus exact-identifier neighbors, and resolves same-object
evidence across batches and sources. Exact identifiers and canonical URLs are identity evidence;
lexical similarity, source, time, stars or engagement may select records for inspection but never
decide a route. Every semantic Research object remains Research regardless of its execution position.

The formal output remains deliberately small:

- `decisions.jsonl`: one final `research/watch/archive` decision per unit. Research has a concrete
  object and one-sentence reason; Watch has one sentence; Archive carries no editorial rewrite.
- `objects.json`: every Research object with all same-object Research units and any explicitly linked
  Watch support. Each object must contain at least one Research unit; its list position is not an
  execution decision.

Phase 2 does not write research scope, questions, evidence plans, scores, per-item summaries or
report structure. Phase 3 owns those decisions. The application validates exact decision coverage,
object membership, hashes and the final Editor session; historical Phase 2 contracts remain readable.

## Phase 3 contract

When Research objects exceed the configured limit, one isolated Phase 3 admission selector reads all
objects and their complete units, then currently selects at most 15 top-level leads with concurrency
3. It cannot modify Phase 2 routes or membership. `phase3_admission.json` records the full available
list, selected priority order, and unscheduled complement. Unscheduled objects keep their Phase 2
Research route and are not automatically carried into later days. Changing the limit or admission
model never changes Phase 2 artifacts; when all objects fit, admission selects all without a model call.

Each admitted object is mechanically adapted to the formal Phase 3 package interface; this adapter
adds no scope or editorial decision. Each package lead receives `AGENTS.md`,
`RESEARCH_METHOD.md`, a readable `PACKAGE.md`, bounded catalog
shards, per-unit source files, an on-demand daily catalog, bootstrap index and progress checkpoint. It may use up to four
non-recursive subagents for genuinely independent research directions.

The lead writes a self-contained Simplified-Chinese `main_report.md`; it creates subreports only when
an independent evidence chain or technical section naturally warrants one. Internal `intake.jsonl`
proves every package unit was inspected, while `evidence.jsonl` distinguishes verified facts, source
claims, inference, disputes and unknowns. Professional terminology, analogy and ASCII are available
when they materially improve understanding.

The artifact manifest and intake must account for every package unit exactly once. Code validates
structure, paths, IDs and link closure only; it does not grade prose or launch a semantic reviewer.
Infrastructure failures retain checkpoints, while valid completed package artifacts are not rerun.
After doing the research, a Lead may return `status=not_published` with no reader page when the
package offers no material insight for the configured reader; intake and evidence remain durable.

For every Phase 2 object, Phase 3 must produce exactly one outcome: a validated main report, a
validated `not_published` decision, or an explicit failure. Legacy `report.md` is readable for old
runs but is rejected for new attention-editor objects. The Phase 3 quality receipt lists every
validated artifact manifest and must agree with those outcomes.

## Phase 4 contract

Phase 4 receives validated reports, Phase 3 quality/failures, `not_published` decisions, source
health, and compact Watch rows. It never receives authority to change Phase 2 routes or Phase 3
facts. Its own `quality.json` records required and linked report IDs, missing links, Watch count, and
whether output came from Codex or the deterministic fallback. Internal unit IDs are rejected from
the reader-facing Brief.

## Queue and permissions

The collector seals Phase 1 locally, copies all referenced blobs plus compact bootstrap/history
indexes into staging, and atomically renames the job into the queue. The worker writes Phase 2-4
artifacts; recovery validates and imports either V3 artifacts or legacy V2 reports before publishing.
Transient non-zero Codex exits preserve the job and checkpoints in `retry_wait` with bounded
backoff. Lark failures remain in `publish_pending`; the recovery heartbeat retries only after their
due time. Process locks make tick, worker and publisher recovery single-owner operations.

The publisher validates all formal Phase 3 ledgers, exact object outcomes, Phase 3/4 quality files,
Watch counts, local files and internal links before its first external write. Year and
month nodes are materialized navigation indexes; day, main-report and subreport pages contain deterministic
back-navigation. Only stale content nodes recorded in the same run's publish manifest may be removed.
The idempotency artifact hash includes research quality, `not_published` and Phase 4 quality so a
quality-state change cannot reuse a stale notification key. Isolated preflight writes a local
`05_publish/preflight_receipt.json` and makes no Lark call.

Operational notification is independent from content publication. A valid run publishes the Brief
and sends its result DM; a retryable or terminal failure sends a Feishu status DM even when the Wiki
tree is not publishable. Every notification has a state-specific idempotency key and durable receipt;
failed sends are retried by the normal recovery tick.

Every Codex call denies the user's Codex data, SSH directory and login Keychains while granting only
the current phase workspace. Installation performs a consistent SQLite backup, idempotent schema
migration, model-access preflight, exact sandbox probe and browser launch check before cutover.
