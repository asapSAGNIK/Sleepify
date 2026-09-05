#!/bin/bash
set -e
systemctl --user disable --now stayawake.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/stayawake.service"
systemctl --user daemon-reload 2>/dev/null || true
pkill -f "linux/stayawake.py" 2>/dev/null || true
# release inhibit
pkill -f "systemd-inhibit.*StayAwake" 2>/dev/null || true
echo "StayAwake uninstalled (linux)."
