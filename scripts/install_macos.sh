#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
runtime_dir=${AI_DIGEST_RUNTIME_ROOT:-$HOME/Library/Application Support/ai-digest}
queue_dir=${AI_DIGEST_SHARED_RUNTIME_ROOT:-$runtime_dir/queue}
mode=${1:-}
user_uid=$(id -u)
launch_domain="gui/$user_uid"
launch_agents="$HOME/Library/LaunchAgents"
legacy_dir="$runtime_dir/legacy-launchagents"

tick_target="$launch_agents/com.ai-digest.tick.plist"
recover_target="$launch_agents/com.ai-digest.recover.plist"
runner_target="$launch_agents/com.ai-digest.agent-runner.plist"

if [[ "$mode" == "--cutover" ]]; then
  for target in "$tick_target" "$recover_target" "$runner_target"; do
    [[ -f "$target" ]] || { echo "Missing installed plist: $target" >&2; exit 2; }
  done
  launchctl bootout "$launch_domain/com.ai-digest" 2>/dev/null || true
  launchctl bootout "$launch_domain/com.ai-digest.daily" 2>/dev/null || true
  launchctl bootout "$launch_domain/com.ai-digest.tick" 2>/dev/null || true
  launchctl bootout "$launch_domain/com.ai-digest.recover" 2>/dev/null || true
  launchctl bootout "$launch_domain/com.ai-digest.agent-runner" 2>/dev/null || true
  mkdir -p "$legacy_dir"
  cutover_stamp=$(date -u +%Y%m%dT%H%M%SZ)
  for legacy_name in com.ai-digest.plist com.ai-digest.daily.plist; do
    legacy_plist="$launch_agents/$legacy_name"
    if [[ -f "$legacy_plist" ]]; then
      mv "$legacy_plist" "$legacy_dir/$legacy_name.disabled-$cutover_stamp"
    fi
  done
  launchctl bootstrap "$launch_domain" "$tick_target"
  launchctl bootstrap "$launch_domain" "$recover_target"
  launchctl bootstrap "$launch_domain" "$runner_target"
  echo "Cut over to V2 user LaunchAgents. Legacy plists are archived in $legacy_dir"
  exit 0
fi

if [[ "$mode" == "--rollback" ]]; then
  launchctl bootout "$launch_domain/com.ai-digest.tick" 2>/dev/null || true
  launchctl bootout "$launch_domain/com.ai-digest.recover" 2>/dev/null || true
  launchctl bootout "$launch_domain/com.ai-digest.agent-runner" 2>/dev/null || true
  for legacy_name in com.ai-digest.plist com.ai-digest.daily.plist; do
    archived=$(find "$legacy_dir" -maxdepth 1 -type f \
      -name "$legacy_name.disabled-*" -print 2>/dev/null | sort | tail -1)
    if [[ -n "$archived" ]]; then
      cp "$archived" "$launch_agents/$legacy_name"
      launchctl bootstrap "$launch_domain" "$launch_agents/$legacy_name"
    fi
  done
  echo "Rolled back to the most recent archived V1 LaunchAgents."
  exit 0
fi

if [[ "$mode" != "" && "$mode" != "--apply" ]]; then
  echo "Usage: $0 [--apply|--cutover|--rollback]" >&2
  exit 2
fi

revision=$(git -C "$project_dir" rev-parse --short=12 HEAD)
release_stamp=$(date -u +%Y%m%dT%H%M%SZ)
app_root="$runtime_dir/apps"
shared_app="$app_root/app-$revision-$release_stamp"
app_staging="$app_root/.app-$revision-$release_stamp.staging"

tick_tmp=$(mktemp /tmp/com.ai-digest.tick.XXXXXX)
recover_tmp=$(mktemp /tmp/com.ai-digest.recover.XXXXXX)
runner_tmp=$(mktemp /tmp/com.ai-digest.agent-runner.XXXXXX)

sed -e "s|__PROJECT__|$shared_app|g" \
    -e "s|__PYTHON__|$shared_app/.venv/bin/python|g" \
    -e "s|__RUNTIME__|$runtime_dir|g" \
    -e "s|__SHARED__|$queue_dir|g" \
    -e "s|__HOME__|$HOME|g" \
    "$project_dir/deploy/com.ai-digest.tick.plist.example" > "$tick_tmp"
sed -e "s|__PROJECT__|$shared_app|g" \
    -e "s|__PYTHON__|$shared_app/.venv/bin/python|g" \
    -e "s|__RUNTIME__|$runtime_dir|g" \
    -e "s|__SHARED__|$queue_dir|g" \
    -e "s|__HOME__|$HOME|g" \
    "$project_dir/deploy/com.ai-digest.recover.plist.example" > "$recover_tmp"
sed -e "s|__PROJECT__|$shared_app|g" \
    -e "s|__PYTHON__|$shared_app/.venv/bin/python|g" \
    -e "s|__SHARED__|$queue_dir|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__CODEX_HOME__|$HOME/.codex|g" \
    "$project_dir/deploy/com.ai-digest.agent-runner.plist.example" > "$runner_tmp"

plutil -lint "$tick_tmp"
plutil -lint "$recover_tmp"
plutil -lint "$runner_tmp"

if [[ "$mode" != "--apply" ]]; then
  echo "Dry run passed. No account, service, or plist was changed."
  echo "Re-run with --apply to install files without enabling schedules."
  echo "Use --cutover only after the manual full-pipeline acceptance run."
  exit 0
fi

git -C "$project_dir" diff --quiet
git -C "$project_dir" diff --cached --quiet
mkdir -p "$runtime_dir/logs" "$app_root" "$launch_agents"
install -d -m 700 "$queue_dir"
for queue_name in staging jobs completed publish_pending archived failed logs; do
  install -d -m 700 "$queue_dir/$queue_name"
done
install -d -m 700 "$app_staging"
git -C "$project_dir" archive HEAD | tar -x -C "$app_staging"
for config_name in runtime.toml sources.toml interests.md twitter_cookies.json; do
  config_source="$project_dir/config/$config_name"
  if [[ -f "$config_source" ]]; then
    install -m 600 "$config_source" "$app_staging/config/$config_name"
  fi
done
cd "$app_staging"
uv sync --no-editable
"$app_staging/.venv/bin/python" -m playwright install chromium
npm ci --ignore-scripts
cd "$project_dir"
chmod -R u=rwX,go= "$app_staging"
mv "$app_staging" "$shared_app"

"$shared_app/.venv/bin/python" -c 'import ai_digest'
"$shared_app/node_modules/.bin/codex" -c 'cli_auth_credentials_store="file"' login status
[[ -s "$HOME/.codex/auth.json" ]] || {
  echo "Current-user file-backed Codex auth is required." >&2
  exit 2
}

probe_dir="$queue_dir/staging/permission-probe-$release_stamp"
install -d -m 700 "$probe_dir"
permission_definition='permissions.ai_digest_probe={extends=":workspace",filesystem={":root"="deny",":minimal"="read",":slash_tmp"="deny"}}'
set +e
probe_output=$("$shared_app/node_modules/.bin/codex" sandbox \
  -c "$permission_definition" -P ai_digest_probe -C "$probe_dir" \
  /bin/sh -c "printf ok > workspace-ok; /bin/dd if='$HOME/.codex/auth.json' of=/dev/null bs=1 count=0" \
  2>&1)
probe_status=$?
set -e
if [[ "$probe_status" -eq 0 || ! -f "$probe_dir/workspace-ok" \
      || "$probe_output" != *"Operation not permitted"* ]]; then
  echo "Same-user workspace-only permission probe failed closed:" >&2
  echo "$probe_output" >&2
  exit 2
fi
rm -rf "$probe_dir"

install -m 600 "$tick_tmp" "$tick_target"
install -m 600 "$recover_tmp" "$recover_target"
install -m 600 "$runner_tmp" "$runner_target"
echo "Installed V2 files for the current user; schedules are still disabled."
echo "Run the full manual acceptance, then execute: $0 --cutover"
