# Setup and rollout

## 1. Configure source files

Copy the three example files under `config/`. The copies are ignored by Git.

GitHub uses `GH_TOKEN`/`GITHUB_TOKEN` when present and otherwise the authenticated `gh` keyring.

For the X private List:

1. Create an X Developer App and enable OAuth2 PKCE user context.
2. Configure its callback URL as `http://127.0.0.1:8765/callback`, export
   `AI_DIGEST_X_CLIENT_ID`, then run `uv run ai-digest x-auth`. Access and refresh tokens are stored
   in macOS Keychain under `ai-digest-x`.
3. Create an empty private List, set `x_list.list_id`, and preview membership construction with
   `uv run ai-digest x-list-bootstrap`. Inspect the JSON plan and estimated cost before rerunning
   with `--apply`.
4. Add prepaid credits and keep auto-recharge disabled during the pilot.
5. Enable the collector only after `doctor` succeeds.

`x_list.compliance_verified` is a fail-closed production gate, not a user acknowledgment checkbox.
Leave it `false` until deletion/update propagation has an end-to-end test covering the local run,
shared queue/archive, Codex context and published Lark copies.

X currently exposes no official API for the personalized For You feed, and its developer guidance
prohibits browser automation. Keep `x_for_you.enabled=false` in production. The Playwright adapter
is retained only as disabled experimental code; it is not part of production acceptance. Use the
official private List API as the compliant X source.

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

## 3. Create the isolated runner

Create a standard macOS user named `ai-digest-runner`. Do not place personal projects, SSH keys,
browser profiles or publisher/source credentials in that home. Log Codex in once as that user.

For a headless runner, OpenAI's documented fallback permits copying the local `auth.json` cache to
the trusted runner. This installer only does so when explicitly requested, never prints the token,
and copies no other Codex config, memory, skill, SSH or browser data:

```bash
AI_DIGEST_COPY_CODEX_AUTH=1 ./scripts/install_macos.sh --apply
```

Without that flag, authenticate the runner first with
`codex -c 'cli_auth_credentials_store="file"' login --device-auth`; installation
fails closed if `codex login status` is not healthy under the runner identity.

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

Apply mode requires administrator access. It creates a credential-free, versioned application copy
under `/Users/Shared/ai-digest-runtime`, unloads the two legacy LaunchAgents and moves their exact
plist files into the recoverable `legacy-launchagents/` archive, then installs three
separate launchd jobs:

- `com.ai-digest.tick` is calendar-only and performs scheduled collection.
- `com.ai-digest.recover` watches `completed/` and only runs `tick --event recover`; it never starts
  a second collection.
- `com.ai-digest.agent-runner` watches `jobs/` and runs Phase 2–4 as the isolated standard user.

The installer creates `staging/`, `jobs/`, `completed/`, `publish_pending/`, `archived/`, `failed/`
and `logs/` as setgid shared queues. The versioned runner app is read-only after dependency setup,
while executable bits in its virtual environment and Node binaries are preserved.

The calendar job polls GitHub at 01:00/07:00/13:00/19:00, the official X List at
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

Run the first seven days as a pilot. Review source counts, partial failures, r/w/n distribution,
bundle count, Codex usage, report size, X usage/cost and Lark publish status before changing source
volume, contexts or concurrency.
