#!/bin/bash
# Install AI Digest as a macOS launchd service
# This will start the scheduler on boot and keep it running

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.ai-digest.plist"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "AI Digest Service Installer"
echo "==========================="
echo ""

# Create logs directory
mkdir -p "$PROJECT_DIR/logs"
echo "Created logs directory"

# Stop existing service if running
if launchctl list | grep -q "com.ai-digest"; then
    echo "Stopping existing service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Copy plist to LaunchAgents
echo "Installing service..."
cp "$PLIST_SRC" "$PLIST_DST"

# Load the service
echo "Starting service..."
launchctl load "$PLIST_DST"

# Check status
echo ""
echo "Service installed successfully!"
echo ""
echo "Status:"
launchctl list | grep "com.ai-digest" || echo "  Service not found (may take a moment to start)"

echo ""
echo "Useful commands:"
echo "  Check status:  launchctl list | grep ai-digest"
echo "  View logs:     tail -f $PROJECT_DIR/logs/scheduler.log"
echo "  Stop service:  launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Start service: launchctl load ~/Library/LaunchAgents/$PLIST_NAME"
echo ""
