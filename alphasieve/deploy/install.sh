#!/usr/bin/env bash
# Install or refresh AlphaSieve systemd units. Usage: deploy/install.sh [unit ...]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
units=("$@")
if [ ${#units[@]} -eq 0 ]; then
  units=(alphasieve-daily-update.service alphasieve-daily-update.timer
         alphasieve-state-backup.service alphasieve-state-backup.timer)
fi
for unit in "${units[@]}"; do
  install -m 0644 "$here/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
for unit in "${units[@]}"; do
  case "$unit" in
    *.timer) systemctl enable --now "$unit" ;;
    *.service) grep -q '^\[Install\]' "$here/systemd/$unit" && systemctl enable --now "$unit" || true ;;
  esac
done
systemctl list-timers --all | grep -E 'alphasieve' || true
