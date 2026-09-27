#!/usr/bin/env bash
# Start N platform evaluation worker processes that serve the taijifs queue (default 8, exit after 4 idle hours).
# Pair with the local bridge: systemctl start alphasieve-evalbridge
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
n="${1:-8}"
idle="${2:-14400}"
remote="${ALPHASIEVE_REMOTE_ROOT:-/taijifs_zw35/r2/felixjjiang/alphasieve}"
ALPHASIEVE_EVAL_POLL=1.0 exec "$here/submit.sh" evalworkers evalsvc worker --queue "$remote/evalq" --processes "$n" --idle-exit "$idle"
