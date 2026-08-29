#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
runtime_dir=${AI_DIGEST_RUNTIME_ROOT:-$HOME/Library/Application Support/ai-digest}
shared_dir=/Users/Shared/ai-digest-runtime
apply_mode=${1:-}

if ! id ai-digest-runner >/dev/null 2>&1; then
  echo "Create the standard macOS user 'ai-digest-runner' first, then log Codex in once." >&2
  exit 2
fi

mkdir -p "$runtime_dir/logs" "$shared_dir/jobs" "$shared_dir/completed" "$shared_dir/logs"
revision=$(git -C "$project_dir" rev-parse --short HEAD)
shared_app="$shared_dir/app-$revision"

tick_target="$HOME/Library/LaunchAgents/com.ai-digest.tick.plist"
daemon_target="/Library/LaunchDaemons/com.ai-digest.agent-runner.plist"
tick_tmp=$(mktemp /tmp/com.ai-digest.tick.XXXXXX.plist)
daemon_tmp=$(mktemp /tmp/com.ai-digest.agent-runner.XXXXXX.plist)

sed -e "s|__PROJECT__|$project_dir|g" \
    -e "s|__PYTHON__|$shared_app/.venv/bin/python|g" \
    -e "s|__RUNTIME__|$runtime_dir|g" \
    "$project_dir/deploy/com.ai-digest.tick.plist.example" > "$tick_tmp"
sed -e "s|__PROJECT__|$project_dir|g" \
    -e "s|__PYTHON__|$project_dir/.venv/bin/python|g" \
    -e "s|__SHARED__|$shared_dir|g" \
    "$project_dir/deploy/com.ai-digest.agent-runner.plist.example" > "$daemon_tmp"

plutil -lint "$tick_tmp"
plutil -lint "$daemon_tmp"

if [[ "$apply_mode" != "--apply" ]]; then
  echo "Dry run passed. Re-run with --apply to install launchd jobs."
  echo "Tick plist: $tick_tmp"
  echo "Runner plist: $daemon_tmp"
  exit 0
fi

sudo mkdir -p "$shared_app"
sudo chown -R "$USER":staff "$shared_dir"
sudo chmod -R g+rwX,o-rwx "$shared_dir"
git -C "$project_dir" archive HEAD | tar -x -C "$shared_app"
cd "$shared_app"
uv sync
npm ci --ignore-scripts
cd "$project_dir"

install -m 600 "$tick_tmp" "$tick_target"
sudo install -o root -g wheel -m 644 "$daemon_tmp" "$daemon_target"
launchctl bootout "gui/$(id -u)/com.ai-digest" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.ai-digest.daily" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.ai-digest.tick" 2>/dev/null || true
sudo launchctl bootout system/com.ai-digest.agent-runner 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$tick_target"
sudo launchctl bootstrap system "$daemon_target"
echo "Installed com.ai-digest.tick and com.ai-digest.agent-runner"
