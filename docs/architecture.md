# Architecture and data contracts

## Reader-first pipeline

```text
platform observations -> sealed source JSONL -> exact observation units
  -> one serial Phase 2 Codex thread -> 0-15 research packages
  -> package research leads -> Chinese dossiers/subreports
  -> navigation brief -> private Lark Wiki + self-DM
```

Phase 1 maximizes durable observation coverage. Phase 2 annotates and dispatches without browsing or
research. Phase 3 verifies concrete changes and explains them faithfully; it does not have to invent
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
  02_routing/{units.jsonl,annotations.jsonl,working_map.md,packages.json}
  03_research/<package-id>/{dossier.md,subreports/,research_manifest.json}
  04_brief/daily_brief.md
  05_publish/publish_manifest.json
state.db
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
- arXiv consumes the complete configured multi-category daily feed. Hugging Face Daily Papers is a
  dated, paginated attention/enrichment surface. They remain separate observations until unitization.
- Hacker News scans item IDs from the durable `maxitem` cursor. New/top/show are attention surfaces
  on one story identity, not three story records.
- Media adapters retain raw discovery/body responses, normalize canonical URLs, recurse sitemap
  indexes and emit content-hash revisions. Extraction failures preserve metadata for Phase 3.

`SourceHealth` records operational status separately from `coverage_mode`:
`complete_increment`, `bounded_discovery`, or `sampled_surface`. Receipts include pagination/cursor
state, duplicate/revision counts, observed time range, raw completeness and quiet rationale.

## Phase 2 contract

Deterministic unitization groups only provably identical entities: an X post/conversation, GitHub
repo, arXiv paper, HN story or canonical article. Semantic similarity never deletes evidence.

One logical Codex thread processes bounded batches of at most 160 units and 256 KiB projection.
Each batch returns exactly one annotation per unit plus a compact working map:

```json
{"unit_id":"u_...","disposition":"investigate","summary_zh":"...","reason":"...","entities":[],"relation_hints":[],"duplicate_of":null}
```

Allowed dispositions are `investigate`, `supporting`, `duplicate`, and `discard`. Ambiguous weak
signals default to investigation. The application validates each checkpoint before resuming the
same thread and never performs the V2 all-item calibration pass.

After all annotations, the same thread reads only compact artifacts and creates 0-15 packages.
Every investigate unit belongs to exactly one package. Packages are split mechanically above 90
primary units or about 750 KiB.

## Phase 3 contract

Each package lead receives `AGENTS.md`, a readable `PACKAGE.md`, a manifest, per-unit source files,
an on-demand daily catalog, bootstrap index and progress checkpoint. It may use up to four
non-recursive subagents for genuinely independent research directions.

The lead writes a Simplified-Chinese `dossier.md`, natural `subreports/*.md`, and a small artifact
manifest. Every subreport identifies its triggering daily changes, verified facts, important detail,
primary evidence, conflicts and unknowns. Relationships may use ASCII when useful.

The artifact manifest accounts for each investigate unit as primary or unresolved. Missing units
mark Phase 3 partial and are recorded for prompt/context evaluation; production does not launch an
automatic patch agent. Existing research still publishes.

## Queue and permissions

The collector seals Phase 1 locally, copies all referenced blobs plus compact bootstrap/history
indexes into staging, and atomically renames the job into the queue. The worker writes Phase 2-4
artifacts; recovery validates and imports either V3 artifacts or legacy V2 reports before publishing.

Every Codex call denies the user's Codex data, SSH directory and login Keychains while granting only
the current phase workspace. Installation performs a consistent SQLite backup, idempotent schema
migration, model-access preflight, exact sandbox probe and browser launch check before cutover.
