# Architecture and data contracts

## Runtime truth

The runtime root defaults to `~/Library/Application Support/ai-digest`:

```text
store/
  blobs/<sha256>
  fetches/<source>/<date>/<fetch-id>/manifest.json
  revisions/<source>/<item-id>/<revision>.json
  revisions/github_snapshots/<repo-id>/<timestamp>-<snapshot-id>.json
runs/<date>/attempt-0001/
  00_run_manifest.json
  01_phase1/{x_list,x_for_you,github,papers,articles,hackernews}.jsonl
  02_routing/{bundles.json,assignments.jsonl}
  03_research/<bundle-id>/report.md
  04_brief/daily_brief.md
  05_publish/publish_manifest.json
state.db
```

Files are canonical. SQLite stores cursors, delivery state and rebuildable indexes.

GitHub candidate polls run at 01:00, 07:00, 13:00 and 19:00 local time. A delta is `null`
until an observation exists at or before the full 6h/24h/7d horizon; the first observation is
never presented as growth. Recently observed early-lane repositories are rotated through direct
core-API checks so crossing 500 stars cannot disappear between search lanes.

## SourceItem

All six JSONL files use a thin common envelope and a typed, source-specific payload:

```json
{
  "schema_version": 1,
  "item_id": "hackernews:show:123",
  "item_type": "hn_story",
  "source": "hackernews",
  "surface": "show",
  "change": "entered_surface",
  "occurred_at": "2026-08-29T23:00:00Z",
  "first_observed_at": "2026-08-30T00:01:00Z",
  "handoff_at": "2026-08-30T00:01:00Z",
  "time_basis": "observed",
  "content_status": "full",
  "raw_refs": ["sha256:...json"],
  "payload": {}
}
```

Content streams use the source's publication/version time. Recommendation and ranking surfaces use
their first local observation time because those platforms do not expose an entry event. Content
older than the 24-hour batch window remains in the store but is not automatically handed off.

## Longitudinal deduplication

- X: `post_id + surface`; official edits may create a new revision.
- GitHub: `numeric_repo_id + lane`; entering early, emerging and Trending are separate firsts.
- arXiv: `arxiv_id + version`.
- Hugging Face: `arxiv_id + daily_papers surface`.
- HN: `story_id + new/top/show surface`.
- Articles: `canonical URL + clean text hash`.

Normal engagement changes do not trigger a new SourceItem. GitHub ordinary pushes and releases do
not turn the discovery system into an unbounded long-term repository watcher.

## Phase 2 contract

Every Phase 1 `item_id` must appear exactly once in `assignments.jsonl`:

```json
{"id":"...","d":"r","t":["bundle-1"]}
{"id":"...","d":"w","t":[]}
{"id":"...","d":"n","t":[]}
```

Research items may enter one or two bundles. A valid quiet day has zero bundles and a reason. Code
validates coverage and asks the same Router session to repair an incomplete output once.

## Queue isolation

The main LaunchAgent first seals an immutable Phase 1 run in SQLite, materializes it under the
unwatched `staging/` directory, and atomically renames it into `jobs/`. Only after that queue
directory is visible does one transaction mark the run queued and its items delivered. Sealed runs
are replayed at the beginning of every tick. A user LaunchAgent moves the job to `completed/` after
Phase 2–4. A distinct recovery LaunchAgent watches only `completed/`, imports the artifacts and publishes them; its entry
point is `tick --event recover`, so a queue wake cannot start another collection. Successful jobs
move to `archived/`, Lark retryable jobs to `publish_pending/`, and failed worker jobs to `failed/`.

Every Codex call uses a custom permission profile rather than the broad built-in sandbox flag. The
profile denies reads of the current user's entire `CODEX_HOME`, `.ssh`, and login Keychains while granting
only read-only or current-workspace access as the phase requires. Installation fails closed unless
an exact sandbox probe can write its workspace but receives `Operation not permitted` when opening
`auth.json`, including a zero-byte read.
