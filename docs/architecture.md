# Architecture and data contracts

## Reader-first pipeline

```text
platform observations -> sealed source JSONL -> exact observation units
  -> one serial Phase 2 Codex thread -> 0-15 research packages
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
  02_routing/{units.jsonl,catalog.jsonl,working_map.md,packages.json,phase2_manifest.json}
  03_research/<package-id>/{main_report.md,intake.jsonl,evidence.jsonl,subreports/,research_manifest.json}
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

One new Codex thread is created for each day and resumed across bounded batches of at most 160 units
and 256 KiB projection. Each batch returns exactly one factual Chinese summary per unit plus a compact
working map:

```json
{"unit_id":"u_...","summary_zh":"...","group_id":"dynamic-topic-key"}
```

Phase 2 does not decide importance, research depth, questions or conclusions. The application
persists the thread as soon as Codex starts it, hashes every input checkpoint, and rejects mixed
threads or stale outputs.

As it reads, the Agent reuses or creates dynamic group IDs and carries their meanings in the working
map. After all batches, the same thread consolidates every observed group into 1–15 semantic
packages. Code only expands group membership back to units and validates exact coverage; it does not
perform semantic matching, importance filtering or mechanical package splitting. Large package
catalogs are stored as bounded shards for the Lead.

## Phase 3 contract

Each package lead receives `AGENTS.md`, `RESEARCH_METHOD.md`, a readable `PACKAGE.md`, bounded catalog
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

## Queue and permissions

The collector seals Phase 1 locally, copies all referenced blobs plus compact bootstrap/history
indexes into staging, and atomically renames the job into the queue. The worker writes Phase 2-4
artifacts; recovery validates and imports either V3 artifacts or legacy V2 reports before publishing.
Transient non-zero Codex exits preserve the job and checkpoints in `retry_wait` with bounded
backoff. Lark failures remain in `publish_pending`; the recovery heartbeat retries only after their
due time. Process locks make tick, worker and publisher recovery single-owner operations.

The publisher validates all local files and internal links before its first external write. Year and
month nodes are materialized navigation indexes; day, main-report and subreport pages contain deterministic
back-navigation. Only stale content nodes recorded in the same run's publish manifest may be removed.

Every Codex call denies the user's Codex data, SSH directory and login Keychains while granting only
the current phase workspace. Installation performs a consistent SQLite backup, idempotent schema
migration, model-access preflight, exact sandbox probe and browser launch check before cutover.
