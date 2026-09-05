#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
runtime_dir=${AI_DIGEST_RUNTIME_ROOT:-$HOME/Library/Application Support/ai-digest}
queue_dir=${AI_DIGEST_SHARED_RUNTIME_ROOT:-$runtime_dir/queue}
mode=${1:-}
user_uid=$(id -u)
app_root="$runtime_dir/apps"
control_script="$project_dir/scripts/manage_launchagents.py"
control_python="$project_dir/.venv/bin/python"
[[ -x "$control_python" ]] || control_python=$(command -v python3)
control_args=(
  --runtime "$runtime_dir"
  --queue "$queue_dir"
  --home "$HOME"
  --uid "$user_uid"
)

if [[ "$mode" == "--rollback" ]]; then
  echo "--rollback is deprecated and unsupported; follow the legacy-v1 manual migration guide." >&2
  exit 2
fi

if [[ "$mode" == "--apply" || "$mode" == "--cutover" \
    || "$mode" == "--rollback-v3" ]]; then
  if [[ "${AI_DIGEST_INSTALLER_LOCK_HELD:-0}" != "1" ]]; then
    exec "$control_python" "$control_script" "${control_args[@]}" \
      locked-run --script "${0:A}" --mode="$mode"
  fi
fi

case "$mode" in
  --cutover)
    exec "$control_python" "$control_script" "${control_args[@]}" cutover
    ;;
  --rollback-v3)
    exec "$control_python" "$control_script" "${control_args[@]}" rollback-v3
    ;;
  ""|--apply)
    ;;
  *)
    echo "Usage: $0 [--apply|--cutover|--rollback-v3]" >&2
    exit 2
    ;;
esac

revision=$(git -C "$project_dir" rev-parse --short=12 HEAD)
release_stamp=$(date -u +%Y%m%dT%H%M%SZ)
shared_app="$app_root/app-$revision-$release_stamp"
app_staging="$app_root/.app-$revision-$release_stamp.staging"

tick_tmp=$(mktemp /tmp/com.ai-digest.tick.XXXXXX)
recover_tmp=$(mktemp /tmp/com.ai-digest.recover.XXXXXX)
runner_tmp=$(mktemp /tmp/com.ai-digest.agent-runner.XXXXXX)
cleanup_generated_plists() {
  rm -f "$tick_tmp" "$recover_tmp" "$runner_tmp"
}
trap cleanup_generated_plists EXIT

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
  echo "Dry run passed. No account, service, plist, lock, or runtime file was changed."
  echo "Re-run with --apply to install an immutable snapshot and stage pending LaunchAgents."
  echo "Use --cutover only after the manual full-pipeline acceptance run."
  exit 0
fi

"$control_python" "$control_script" "${control_args[@]}" repair-current
git -C "$project_dir" diff --quiet
git -C "$project_dir" diff --cached --quiet
mkdir -p "$runtime_dir/logs" "$app_root"
install -d -m 700 "$queue_dir"
for queue_name in staging jobs retry_wait completed publish_pending archived failed logs; do
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
uv sync --no-editable --extra semantic
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

if [[ -f "$runtime_dir/state.db" ]]; then
  backup_dir="$runtime_dir/backups"
  install -d -m 700 "$backup_dir"
  state_backup="$backup_dir/state-before-v3-$release_stamp.db"
  sqlite3 "$runtime_dir/state.db" ".backup '$state_backup'"
  chmod 600 "$state_backup"
fi
"$shared_app/.venv/bin/python" -m ai_digest.cli maintenance \
  --classify-existing-article-bootstrap \
  --repair-completed-handoff-ledger

"$shared_app/.venv/bin/python" "$shared_app/scripts/manage_launchagents.py" \
  "${control_args[@]}" stage-pending \
  --tick "$tick_tmp" --recover "$recover_tmp" --runner "$runner_tmp"
echo "Installed V3 files and staged pending LaunchAgents; the current schedule was not changed."
echo "Run the full manual acceptance, then execute: $0 --cutover"
