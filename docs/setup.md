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

For You requires a persistent Playwright profile with the dedicated research account already logged
in. It remains best-effort and policy-risky; the official List source is the stable path.
Run `uv run ai-digest x-login` for the one-time persistent browser login.

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

The installer is dry-run by default:

```bash
./scripts/install_macos.sh
```

After inspecting the generated plist paths:

```bash
./scripts/install_macos.sh --apply
```

Apply mode requires administrator access. It creates a credential-free, versioned application copy
under `/Users/Shared/ai-digest-runtime`, disables the two legacy LaunchAgents, and installs one main
tick LaunchAgent plus one queue-consuming runner LaunchDaemon.

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
