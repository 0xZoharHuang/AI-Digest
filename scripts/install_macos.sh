#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
runtime_dir=${AI_DIGEST_RUNTIME_ROOT:-$HOME/Library/Application Support/ai-digest}
shared_dir=/Users/Shared/ai-digest-runtime
apply_mode=${1:-}
copy_codex_auth=${AI_DIGEST_COPY_CODEX_AUTH:-0}

if ! id ai-digest-runner >/dev/null 2>&1; then
  echo "Create the standard macOS user 'ai-digest-runner' first, then log Codex in once." >&2
  exit 2
fi
if ! id -Gn ai-digest-runner | tr ' ' '\n' | grep -qx staff; then
  echo "The ai-digest-runner user must be a member of the macOS staff group." >&2
  exit 2
fi
runner_home=$(/usr/bin/dscl . -read /Users/ai-digest-runner NFSHomeDirectory | /usr/bin/awk '{print $2}')
if [[ "$runner_home" != "/Users/ai-digest-runner" ]]; then
  echo "Unexpected ai-digest-runner home: $runner_home" >&2
  exit 2
fi
if [[ "$copy_codex_auth" != "0" && "$copy_codex_auth" != "1" ]]; then
  echo "AI_DIGEST_COPY_CODEX_AUTH must be 0 or 1" >&2
  exit 2
fi

revision=$(git -C "$project_dir" rev-parse --short=12 HEAD)
release_stamp=$(date -u +%Y%m%dT%H%M%SZ)
shared_app="$shared_dir/app-$revision-$release_stamp"

tick_target="$HOME/Library/LaunchAgents/com.ai-digest.tick.plist"
recover_target="$HOME/Library/LaunchAgents/com.ai-digest.recover.plist"
daemon_target="/Library/LaunchDaemons/com.ai-digest.agent-runner.plist"
tick_tmp=$(mktemp /tmp/com.ai-digest.tick.XXXXXX.plist)
recover_tmp=$(mktemp /tmp/com.ai-digest.recover.XXXXXX.plist)
daemon_tmp=$(mktemp /tmp/com.ai-digest.agent-runner.XXXXXX.plist)

sed -e "s|__PROJECT__|$project_dir|g" \
    -e "s|__PYTHON__|$project_dir/.venv/bin/python|g" \
    -e "s|__RUNTIME__|$runtime_dir|g" \
    -e "s|__SHARED__|$shared_dir|g" \
    "$project_dir/deploy/com.ai-digest.tick.plist.example" > "$tick_tmp"
sed -e "s|__PROJECT__|$project_dir|g" \
    -e "s|__PYTHON__|$project_dir/.venv/bin/python|g" \
    -e "s|__RUNTIME__|$runtime_dir|g" \
    -e "s|__SHARED__|$shared_dir|g" \
    "$project_dir/deploy/com.ai-digest.recover.plist.example" > "$recover_tmp"
sed -e "s|__PROJECT__|$shared_app|g" \
    -e "s|__PYTHON__|$shared_app/.venv/bin/python|g" \
    -e "s|__SHARED__|$shared_dir|g" \
    "$project_dir/deploy/com.ai-digest.agent-runner.plist.example" > "$daemon_tmp"

plutil -lint "$tick_tmp"
plutil -lint "$recover_tmp"
plutil -lint "$daemon_tmp"

if [[ "$apply_mode" != "--apply" ]]; then
  echo "Dry run passed. Re-run with --apply to install launchd jobs."
  echo "Tick plist: $tick_tmp"
  echo "Recovery plist: $recover_tmp"
  echo "Runner plist: $daemon_tmp"
  exit 0
fi

mkdir -p "$runtime_dir/logs" "$HOME/Library/LaunchAgents"
sudo install -d -o "$USER" -g staff -m 2770 "$shared_dir"
for queue_dir in staging jobs completed publish_pending archived failed logs; do
  sudo install -d -o "$USER" -g staff -m 2770 "$shared_dir/$queue_dir"
done
app_staging="$shared_dir/staging/.app-$revision-$release_stamp.staging"
sudo install -d -o "$USER" -g staff -m 2770 "$app_staging"
git -C "$project_dir" archive HEAD | tar -x -C "$app_staging"
cd "$app_staging"
uv sync --no-editable
npm ci --ignore-scripts
cd "$project_dir"
sudo chown -R root:wheel "$app_staging"
sudo chmod -R u=rwX,go=rX "$app_staging"
sudo mv "$app_staging" "$shared_app"

if [[ "$copy_codex_auth" == "1" ]]; then
  if [[ ! -s "$HOME/.codex/auth.json" ]]; then
    echo "Main-user Codex auth cache is missing; cannot use the approved headless fallback." >&2
    exit 2
  fi
  sudo install -d -o ai-digest-runner -g staff -m 700 "$runner_home/.codex"
  sudo install -o ai-digest-runner -g staff -m 600 \
    "$HOME/.codex/auth.json" "$runner_home/.codex/auth.json"
fi

runner_path=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
sudo -u ai-digest-runner env -i \
  HOME="$runner_home" USER=ai-digest-runner LOGNAME=ai-digest-runner \
  CODEX_HOME="$runner_home/.codex" PATH="$runner_path" \
  "$shared_app/.venv/bin/python" -c 'import ai_digest'
sudo -u ai-digest-runner env -i \
  HOME="$runner_home" USER=ai-digest-runner LOGNAME=ai-digest-runner \
  CODEX_HOME="$runner_home/.codex" PATH="$runner_path" \
  "$shared_app/node_modules/.bin/codex" \
  -c 'cli_auth_credentials_store="file"' login status
if ! sudo -u ai-digest-runner test -s "$runner_home/.codex/auth.json"; then
  echo "Runner must use a file-backed auth cache so model tools can explicitly deny it." >&2
  exit 2
fi

probe_dir="$shared_dir/staging/permission-probe-$release_stamp"
sudo install -d -o ai-digest-runner -g staff -m 2770 "$probe_dir"
permission_definition="permissions.ai_digest_probe={extends=\":workspace\",filesystem={\"$runner_home/.codex\"=\"deny\"}}"
set +e
probe_output=$(sudo -u ai-digest-runner env -i \
  HOME="$runner_home" USER=ai-digest-runner LOGNAME=ai-digest-runner \
  CODEX_HOME="$runner_home/.codex" PATH="$runner_path" \
  "$shared_app/node_modules/.bin/codex" sandbox \
  -c "$permission_definition" -P ai_digest_probe -C "$probe_dir" \
  "$shared_app/.venv/bin/python" -c \
  "from pathlib import Path; Path('workspace-ok').write_text('ok'); open('$runner_home/.codex/auth.json', 'rb').read(0)" \
  2>&1)
probe_status=$?
set -e
if [[ "$probe_status" -eq 0 || ! -f "$probe_dir/workspace-ok" \
      || "$probe_output" != *"Operation not permitted"* ]]; then
  echo "Runner sandbox credential-denial probe failed closed:" >&2
  echo "$probe_output" >&2
  exit 2
fi
sudo rm -rf "$probe_dir"

install -m 600 "$tick_tmp" "$tick_target"
install -m 600 "$recover_tmp" "$recover_target"
sudo install -o root -g wheel -m 644 "$daemon_tmp" "$daemon_target"
launchctl bootout "gui/$(id -u)/com.ai-digest" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.ai-digest.daily" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.ai-digest.tick" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.ai-digest.recover" 2>/dev/null || true
sudo launchctl bootout system/com.ai-digest.agent-runner 2>/dev/null || true
legacy_dir="$runtime_dir/legacy-launchagents"
mkdir -p "$legacy_dir"
for legacy_name in com.ai-digest.plist com.ai-digest.daily.plist; do
  legacy_plist="$HOME/Library/LaunchAgents/$legacy_name"
  if [[ -f "$legacy_plist" ]]; then
    mv "$legacy_plist" "$legacy_dir/$legacy_name.disabled-$release_stamp"
  fi
done
launchctl bootstrap "gui/$(id -u)" "$tick_target"
launchctl bootstrap "gui/$(id -u)" "$recover_target"
sudo launchctl bootstrap system "$daemon_target"
echo "Installed com.ai-digest.tick, com.ai-digest.recover and com.ai-digest.agent-runner"
