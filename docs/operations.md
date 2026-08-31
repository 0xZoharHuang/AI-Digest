# Operations and recovery

This runbook describes the installed macOS service. It does not replace the acceptance sequence in
[Setup and rollout](setup.md).

## Service topology

| LaunchAgent | Trigger | Responsibility |
|---|---|---|
| `com.ai-digest.tick` | login + calendar | due source collection and wake catch-up |
| `com.ai-digest.agent-runner` | login + non-empty `jobs/` | Phase 2–4 worker |
| `com.ai-digest.recover` | login + 15 minutes + non-empty `completed/` | retries, import, Lark publish, missing daily catch-up |

All three are current-user services. `tick`, worker, and recovery each have a non-blocking lock, so
calendar and queue triggers cannot process the same critical section concurrently.

## Default schedule

```text
01:00  X Lists + GitHub + HN
03:00  recovery + X retention maintenance
07:00  full daily collection
07:10  early crash recovery
07:19  early crash recovery
13:00  X Lists + GitHub + HN
13:30  arXiv + HF prefetch
19:00  X Lists + GitHub + HN + paper retry
20:00  second X For You sample
```

`RunAtLoad` and the daily gate compensate for sleep/login. Multiple missed macOS calendar triggers
may coalesce; the application decides what is due from durable state rather than trusting the wall
clock trigger identity.

## Runtime layout

```text
~/Library/Application Support/ai-digest/
├── apps/                 # active + two rollback snapshots
├── backups/              # SQLite pre-migration backups
├── logs/
├── runs/<date>/attempt-*/
├── state.db
├── store/{blobs,fetches,revisions}/
└── queue/
    ├── staging/
    ├── jobs/
    ├── retry_wait/
    ├── completed/
    ├── publish_pending/
    ├── archived/
    └── failed/
```

Do not manually move production jobs while a LaunchAgent is active.

## Normal health checks

```bash
uv run ai-digest doctor

launchctl print "gui/$(id -u)/com.ai-digest.tick"
launchctl print "gui/$(id -u)/com.ai-digest.agent-runner"
launchctl print "gui/$(id -u)/com.ai-digest.recover"

tail -n 100 "$HOME/Library/Application Support/ai-digest/logs/tick.log"
tail -n 100 "$HOME/Library/Application Support/ai-digest/logs/tick.error.log"
tail -n 100 "$HOME/Library/Application Support/ai-digest/queue/logs/agent-runner.error.log"
```

Old error text is not a current failure. Compare modification time and log offset before/after a
run. A healthy idle LaunchAgent is normally `state = not running`, with its last exit code 0.

## Queue recovery

### `staging/`

An enqueue was interrupted before the atomic rename. Old recognized staging directories are removed
after 24 hours; a sealed owner run remains replayable.

### `jobs/`

The worker resumes existing Phase 2 batches and valid Phase 3 package artifacts. `DONE` written before
an interrupted move is recognized on the next worker start.

### `retry_wait/`

Codex non-zero exits preserve the complete job. Default retry delays are 10 minutes, 1 hour, 6 hours,
and 24 hours. The recovery heartbeat promotes due jobs; exhausted retries produce an explicit
failure/partial artifact rather than silently losing the run.

### `completed/`

The owner validates every imported path, schema, unit/package coverage, dossier manifest, and Brief
before publication.

### `publish_pending/`

Lark failures retain the job and use bounded backoff. Already written report/day/index pages are not
rewritten solely because the DM failed. The DM uses an artifact-derived idempotency key.

### `failed/`

Contains quarantined unsafe, duplicate, or invalid-import jobs. Do not requeue one until its
`recovery_error.json`/`worker_failure.json` and corresponding owner run have been audited.

## Laptop outage semantics

When the Mac is off, no local collector runs. After login/wake:

- X Lists resume their provider cursor;
- HN drains a bounded `maxitem` backlog without skipping a failed chunk;
- HF drains dated pages;
- GitHub uses configured multi-day discovery windows;
- media depends on what its RSS/sitemap/index still retains;
- X For You starts a fresh sample and cannot reconstruct history;
- arXiv RSS exact missed announcement history is not reconstructable from `submittedDate` alone.

## Upgrade and rollback

```bash
./scripts/install_macos.sh --apply
uv run ai-digest doctor
uv run ai-digest automation-smoke
./scripts/install_macos.sh --cutover
```

Apply creates a SQLite backup before migration. Cutover loads the new snapshot, then retains the
active build plus two prior snapshots. Old application code can be rebuilt from Git; runtime evidence
and backups are outside the pruning boundary.

To return to the archived legacy LaunchAgents:

```bash
./scripts/install_macos.sh --rollback
```

Do not use `git reset --hard`, delete `state.db`, or clear the queue as a recovery shortcut.
