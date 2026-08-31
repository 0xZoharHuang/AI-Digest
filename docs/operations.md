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
├── apps/                 # active + protected previous V3 + newest retained snapshot
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

The owner validates every imported path, schema, unit/package coverage, research artifact, and Brief
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

Apply creates a SQLite backup before migration, repairs on-disk plists to any safely loaded V3, and
places the new target only in runtime `pending-launchagents/`. A later failed apply cannot expose an
older pending target because apply invalidates it before building. Cutover validates the snapshot
named by the pending tick plist, records a currently loaded immutable V3 as `previous-v3` when one
exists, and loads the new snapshot. Retention protects both snapshots when there is a reverse target.
The pruner then adds the newest remaining
builds until the total retained set reaches three; explicit protections are never dropped merely
because they exceed that count. All mutating installer modes share one non-blocking runtime lock.
Before bootout, cutover validates the target Python/package/pruner, renders and lints both target and
fallback plists, and compares the pending target plists byte for byte. It refuses to interrupt any
known V1/V3 label that is still active and gates the queues again after bootout. A concurrent queue item, partial bootstrap, unexpected
Program/WorkingDirectory, or non-zero label exit triggers a best-effort removal of the new labels and
restoration of the old V3; cutover reports the recovery outcome before returning a failure. Snapshot
pruning does not run on those failure paths, and V1 is never
enabled implicitly. Consumers start before the producer: agent-runner, recover, then tick, with a
bounded health/stability check after each bootstrap. Once all three are healthy, a later pruning error
is only a retention warning: the healthy new V3 stays active and cutover returns success. Cutover
attempts to remove pending plists only after success; a cleanup failure is reported as a warning and
leaves the healthy target active. Runtime evidence and backups are outside the pruning boundary.

To switch to the recorded previous V3 snapshot:

```bash
./scripts/install_macos.sh --rollback-v3
```

The switch requires `staging/`, `jobs/`, `retry_wait/`, `completed/`, and `publish_pending/` to be
empty. It rejects a missing, symlinked, non-snapshot, or out-of-tree target before changing launchd.
The target snapshot's own three deploy templates are rendered and linted, the queues are checked again
after bootout, and all loaded labels are verified before a successful switch records the formerly
active V3 snapshot as the next reverse target. If launchd rejects the target or the reverse-record
write fails, the installer collects and reports the original-V3 restore result and never enables V1
implicitly.

Automatic V1 rollback is unsupported. Cutover fails before changing V3 when any live V1 label or
legacy plist remains; use the [legacy-v1 tag](https://github.com/0xZoharHuang/AI-Digest/tree/legacy-v1)
to stop or migrate it. The
deprecated `--rollback` command exits without creating runtime state. V3 rollback does not restore a
SQLite backup automatically.

Do not use `git reset --hard`, delete `state.db`, or clear the queue as a recovery shortcut.
