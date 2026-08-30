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
direct-message reminder. Publication creates year, month, day and report child nodes, reads its
local manifest before every write, and marks the reminder sent only after Lark returns a real
`message_id` and `chat_id`.

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

The calendar job polls Lists and GitHub at 01:00/13:00/19:00, runs the complete daily collection
(including Lists and For You) at 07:00, and performs a second For You pass at 20:00.
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

Before cutover, verify all three List surfaces, a non-empty For You pass, every non-X adapter, one
complete Chinese Router/Research/Brief run, Wiki readback and a bot DM with a real message ID. Run a
24-hour shadow pilot and review source counts, partial failures, r/w/n distribution, bundle count,
Codex usage, provider credit usage and Lark status before switching from V1.
