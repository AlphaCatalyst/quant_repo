#!/usr/bin/env bash
# Install or refresh AlphaSieve systemd units. Usage: deploy/install.sh [unit ...]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
obsolete=(alphasieve-daily-update.service alphasieve-daily-update.timer
          alphasieve-forward.service alphasieve-forward.timer
          alphasieve-state-backup.service alphasieve-state-backup.timer
          alphasieve-evalworker.service)
for unit in "${obsolete[@]}"; do
  systemctl disable --now "$unit" 2>/dev/null || true
  rm -f "/etc/systemd/system/$unit"
done
units=("$@")
if [ ${#units[@]} -eq 0 ]; then
  units=(alphasieve-control.service alphasieve-control.timer
         alphasieve-failure@.service alphasieve-evalbridge.service
         alphasieve-orchestrator@.service alphasieve-web.service)
fi
for unit in "${units[@]}"; do
  install -m 0644 "$here/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
for unit in "${units[@]}"; do
  case "$unit" in
    *.timer)
      if [[ -e /data/alphasieve/deploy/current ]]; then
        systemctl enable --now "$unit"
      else
        systemctl enable "$unit"
        echo "current release not present; enabled $unit without starting it" >&2
      fi ;;
    alphasieve-web.service|alphasieve-evalbridge.service)
      systemctl enable "$unit"
      if [[ -e /data/alphasieve/deploy/current ]]; then
        systemctl restart "$unit"
      else
        echo "current release not present; enabled $unit without restarting it" >&2
      fi ;;
  esac
done
systemctl list-timers --all | grep -E 'alphasieve' || true
