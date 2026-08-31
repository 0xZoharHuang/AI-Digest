# Setup and rollout

## 1. Configure source files

Copy the three example files under `config/`. The copies are ignored by Git.

GitHub uses `GH_TOKEN`/`GITHUB_TOKEN` when present and otherwise the authenticated `gh` keyring.

For the three public X Lists:

1. Create a TwitterAPI.io account and validate the free starter credits against the configured
   public List IDs.
2. Store the API key in macOS Keychain with `uv run ai-digest x-provider-set-key`.
3. Keep the provider on pay-as-you-go with no subscription or auto-recharge.
4. Each List advances its own `sinceTime` cursor only after all pages and items are durable.

For the personal For You feed, explicitly acknowledge the browser/account risk in local config and
refresh the ignored cookie file with:

```bash
uv run ai-digest x-login
```

The interactive window must visibly land on the English `For you` tab before cookies are saved.
Never commit the cookie file, and expect UI or account challenges to interrupt collection. After
two failures it cools down for six hours. A For You or individual List failure is reported but does
not block successful sources from reaching the daily brief.

## 2. Configure Lark

Using user identity once:

1. Create a private Wiki named `AI Intelligence Radar`.
2. Add the Lark app/bot as an admin of that space.
3. Put `space_id`, receiver `open_id`, and the tenant Wiki base URL in `config/runtime.toml`.
4. Verify user and bot auth with `lark-cli auth status --verify`.

The checked-in default uses user identity for the private personal Wiki and bot identity for the
direct-message reminder. Publication creates year, month, day, dossier and subreport nodes. Year
and month nodes are written as navigation indexes; day, dossier and subreport pages receive
deterministic breadcrumbs. The publisher validates the complete local tree before its first Lark
write, removes only stale report/subreport nodes recorded in that run's own manifest, and marks the
reminder sent only after Lark returns a real `message_id` and `chat_id`.

## 3. Install the main-user runner

The runner deliberately uses the current macOS account so the existing Codex login can be reused.
It does not copy `auth.json`, browser state or Keychain data, and it does not create another user.

Authentication alone is not acceptance. The installer also runs the same custom Codex permission
profile used by agents and refuses to install launchd jobs unless a workspace write succeeds while
a zero-byte `auth.json` open is denied by the macOS sandbox.

The installer is dry-run by default:

```bash
./scripts/install_macos.sh
```

After inspecting the generated plist paths:

```bash
./scripts/install_macos.sh --apply
```

Apply mode needs no administrator access. It creates a credential-free, versioned application copy
under `~/Library/Application Support/ai-digest/apps/app-<revision>-<timestamp>`, creates three user LaunchAgent plist files,
and leaves both legacy and V3 schedules unloaded. Inspect and run a complete manual cycle before the
separate cutover step:

```bash
./scripts/install_macos.sh --cutover
```

Cutover unloads the two legacy LaunchAgents, archives their exact plist files, and bootstraps three
V3 user LaunchAgents:

- `com.ai-digest.tick` runs at login and calendar slots, performing due collection or catch-up.
- `com.ai-digest.recover` watches `completed/`, runs at login and every 15 minutes, promotes due
  transient agent retries, retries Lark publication, and starts the daily catch-up only when no
  current daily run exists.
- `com.ai-digest.agent-runner` runs at login, watches `jobs/`, and runs Phase 2–4 with the custom
  Codex sandbox.

The installer creates `staging/`, `jobs/`, `retry_wait/`, `completed/`, `publish_pending/`,
`archived/`, `failed/` and `logs/` below the current user's runtime directory. Roll back the
schedules and restore the latest archived V1 plist files with
`./scripts/install_macos.sh --rollback`.
After a successful cutover, immutable application snapshots are pruned to the active build plus two
local rollback builds; source evidence, runs, blobs, state backups and Wiki data are never part of
that pruning boundary. `doctor` fails readiness when the runtime volume has less than 5 GiB free.

The calendar job polls X Lists, GitHub and HN at 01:00/13:00/19:00, prefetches dated arXiv/HF
papers at 13:30 (with a 19:00 retry), runs the complete daily collection
(including Lists and For You) at 07:00, and performs a second For You pass at 20:00.
The 07:10 and 07:19 entries are early crash-recovery checks. A delayed wake or login after 07:00
starts the daily run whenever no active/completed run exists; there is no late-start discard.
Transient Codex failures move the intact checkpoint to `retry_wait/` with bounded backoff, and
publication failures remain in `publish_pending/` with bounded retries. At most one tick, worker and
publisher recovery process may own their respective runtime locks.

No local process can observe a source while the Mac is powered off. After login/wake, X Lists resume
from their provider cursor, HN drains its `maxitem` gap in bounded chunks, HF replays dated pages,
GitHub's configured multi-day search windows rediscover candidates, and RSS/sitemaps expose whatever
the publisher still retains. X For You is inherently a fresh sampled surface and cannot reconstruct
missed recommendations. arXiv RSS is the exact daily announcement surface; its submitted-date API
backfill is explicitly a bounded supplement for new papers, not a reconstruction of missed
replacement, withdrawal or cross-list announcements.

## 4. Acceptance sequence

```bash
uv run ai-digest doctor
uv run ai-digest automation-smoke
```

The automation smoke reads seven representative observations from the production ledger but writes
only to a new isolated owner runtime and queue. It runs the real Phase 1 seal, Codex worker,
completed-job import, reconcile, publish preflight and an in-memory Wiki tree/DM transport. It
requires at least one research package and verifies exact coverage, `DONE=complete`, zero Phase 3
missing units, `jobs -> completed -> archived`, navigable non-empty Wiki pages and zero live Lark
calls. Separate generated owner and worker TOML files prevent either process from falling through to
the production queue. The fixture deliberately carries literal U+2028/U+2029 characters across the
JSONL handoff.

The smoke validates orchestration, not live collector coverage. Before cutover, separately verify all
three List surfaces, a non-empty For You pass, every non-X adapter, and the exact LaunchAgent
`QueueDirectories` triggers. For a deliberate live publication acceptance, also verify Wiki readback
and a bot DM with a real message ID. Review source receipts, partial failures, disposition
distribution, package count, Codex usage, provider credit usage and Lark status before switching.
