# Setup and rollout

## 1. Configure source files

Copy the three example files under `config/`. The copies are ignored by Git.

GitHub uses `GH_TOKEN`/`GITHUB_TOKEN` when present and otherwise the authenticated `gh` keyring.

For the X private List:

1. Create an X Developer App and enable OAuth2 PKCE user context.
2. Configure its callback URL as `http://127.0.0.1:8765/callback`, export
   `AI_DIGEST_X_CLIENT_ID`, then run `uv run ai-digest x-auth`. Access and refresh tokens are stored
   in macOS Keychain under `ai-digest-x`.
3. Preview the deduplicated union of the configured seed Lists with
   `uv run ai-digest x-list-bootstrap`. Inspect the member count and estimated API cost before
   rerunning with `--apply`; apply creates the private destination List when no `list_id` is set,
   checkpoints every member, and writes the verified destination ID back to local config.
4. Add prepaid credits and keep auto-recharge disabled during the pilot.
5. Enable the collector only after `doctor` succeeds.

`x_list.compliance_verified` is a fail-closed production gate, not a user acknowledgment checkbox.
Leave it `false` until deletion/update propagation has an end-to-end test covering the local run,
shared queue/archive, Codex context and published Lark copies.

X exposes no official API for the personalized For You feed. The personal Playwright adapter is a
best-effort supplement that reuses `config/twitter_cookies.json`; it is not a substitute for the
official List and may require periodic manual login:

```bash
uv run ai-digest x-login
```

The interactive window must visibly land on the English `For you` tab before cookies are saved.
Keep the risk acknowledgment local, never commit the cookie file, and expect UI or account
challenges to interrupt collection. After two failures it cools down for six hours. A For You
failure is reported but does not block the day; the required official List does.

## 2. Configure Lark

Using user identity once:

1. Create a private Wiki named `AI Intelligence Radar`.
2. Add the Lark app/bot as an admin of that space.
3. Put `space_id`, receiver `open_id`, and the tenant Wiki base URL in `config/runtime.toml`.
4. Verify user and bot auth with `lark-cli auth status --verify`.

The checked-in default uses user identity because it works with a private personal Wiki and the
current OAuth grant. Switch `lark.identity` to `bot` only after the app has the required Wiki and
Docx bot scopes. Publication creates year, month, day and report child nodes, reads its local
manifest before every write, and sends one idempotent direct message.

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
under `~/Library/Application Support/ai-digest/app`, creates three user LaunchAgent plist files,
and leaves both V1 and V2 schedules unloaded. Inspect and run a complete manual cycle before the
separate cutover step:

```bash
./scripts/install_macos.sh --cutover
```

Cutover unloads the two legacy LaunchAgents, archives their exact plist files, and bootstraps three
V2 user LaunchAgents:

- `com.ai-digest.tick` is calendar-only and performs scheduled collection.
- `com.ai-digest.recover` watches `completed/` and only runs `tick --event recover`; it never starts
  a second collection.
- `com.ai-digest.agent-runner` watches `jobs/` and runs Phase 2–4 with the custom Codex sandbox.

The installer creates `staging/`, `jobs/`, `completed/`, `publish_pending/`, `archived/`, `failed/`
and `logs/` below the current user's runtime directory. Roll back the schedules and restore the
latest archived V1 plist files with `./scripts/install_macos.sh --rollback`.

The calendar job runs X Batch Compliance at 01:00/07:00/13:00/19:00 before other work, polls
GitHub at 01:00/07:00/13:00/19:00, the official X List at
03:00/07:00/11:00/15:00/19:00/23:00 when enabled, and runs the complete daily collection at 07:00.
The 07:10 and 07:19 entries are crash-recovery retries: an active/sealed/queued/completed run is a
no-op, while a `running` record older than 18 minutes may be retried before the 07:20 cutoff.

## 4. Acceptance sequence

```bash
uv run ai-digest doctor
AI_DIGEST_RUNTIME_ROOT=/tmp/ai-digest-smoke uv run ai-digest collect --source arxiv --source huggingface
AI_DIGEST_RUNTIME_ROOT=/tmp/ai-digest-smoke uv run ai-digest phase1
uv run ai-digest pipeline                 # no publish
uv run ai-digest publish /path/to/test-run
```

Before cutover, also run `uv run ai-digest x-compliance` against the live developer App, verify an
exact test Post deletion reaches local runs, queue copies, Codex artifacts and the corresponding
Lark day, then rebuild and republish that day. Do not set `x_list.compliance_verified=true` before
this evidence exists.

Run the first seven days as a pilot. Review source counts, partial failures, r/w/n distribution,
bundle count, Codex usage, report size, X usage/cost and Lark publish status before changing source
volume, contexts or concurrency.
