#!/bin/bash
set -e
PLIST="$HOME/Library/LaunchAgents/com.stayawake.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
pkill -f "mac/stayawake.py" 2>/dev/null || true
# release caffeinate
pkill caffeinate 2>/dev/null || true
echo "StayAwake uninstalled (mac). Logs kept at ~/Library/Application Support/StayAwake/"
