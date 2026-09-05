#!/bin/bash
# StayAwake Linux installer — systemd --user (best practice)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY=$(which python3 || which python)
if [ -z "$PY" ]; then echo "python3 not found"; exit 1; fi
echo "Installing deps..."
"$PY" -m pip install -r "$DIR/linux/requirements.txt" --quiet --disable-pip-version-check || true
# Try playerctl
if ! command -v playerctl >/dev/null 2>&1; then echo "Hint: sudo apt install playerctl (for Spotify MPRIS)"; fi
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/stayawake.service" <<EOF
[Unit]
Description=StayAwake — Spotify-aware lid-close control
After=graphical-session.target

[Service]
ExecStart=$PY $DIR/linux/stayawake.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now stayawake.service
echo "StayAwake installed (systemd --user). Logs: ~/.config/StayAwake/stayawake.log or journalctl --user -u stayawake"
echo "Hotkey: ctrl+shift+space"
