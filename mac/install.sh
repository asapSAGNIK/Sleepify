#!/bin/bash
# StayAwake macOS installer — launchd (best practice, no admin needed for user agent)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY=$(which python3 || which python)
if [ -z "$PY" ]; then echo "python3 not found"; exit 1; fi
echo "Installing deps..."
"$PY" -m pip install -r "$DIR/mac/requirements.txt" --quiet --disable-pip-version-check || true
PLIST="$HOME/Library/LaunchAgents/com.stayawake.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.stayawake</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/mac/stayawake.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Application Support/StayAwake/stayawake.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Application Support/StayAwake/stayawake.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "StayAwake installed (launchd). Logs: ~/Library/Application Support/StayAwake/stayawake.log"
echo "Hotkey: cmd+shift+space"
