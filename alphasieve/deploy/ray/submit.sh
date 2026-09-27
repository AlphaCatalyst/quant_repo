#!/usr/bin/env bash
# Submit an AlphaSieve command as a Ray job on the platform cluster.
# Usage: deploy/ray/submit.sh <job-name> <alphasieve args...>
# The code is uploaded with the job; data and outputs live under ALPHASIEVE_REMOTE_ROOT on taijifs.
# Only the dev panel may exist under the remote root: holdout and fresh data never leave the local host.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
name="${1:?job name}"; shift
export RAY_ADDRESS="${RAY_ADDRESS:-http://28.83.35.117:8081}"
remote_root="${ALPHASIEVE_REMOTE_ROOT:-/taijifs_zw35/r2/felixjjiang/alphasieve}"
job_id="alphasieve-${name}-$(date +%Y%m%d-%H%M%S)"
runtime_env=$(python3 -c '
import json, sys
print(json.dumps({"working_dir": sys.argv[1], "excludes": [".venv/**", "frontend/node_modules/**", ".pytest_cache/**",
                  ".ruff_cache/**", "**/__pycache__/**", "src/alphasieve/web/dist/**"]}))' "$here")

args=$(printf '%q ' "$@")
script=$(cat <<EOF
set -euo pipefail
export UV_INDEX_URL=https://mirrors.tencent.com/pypi/simple/ UV_LINK_MODE=copy
venv=/tmp/alphasieve-venv-\$(sha256sum pyproject.toml | cut -c1-12)
[ -x "\$venv/bin/alphasieve" ] || { uv venv -q -p 3.12 "\$venv" && uv pip install -q --python "\$venv/bin/python" -e . ; }
export ALPHASIEVE_ROLE=system ALPHASIEVE_USER=ray:${job_id} ALPHASIEVE_STORE_MOUNT=
export ALPHASIEVE_HOT_ROOT=${remote_root}/hot ALPHASIEVE_STORE_ROOT=${remote_root}/store
if [ -e "\$ALPHASIEVE_HOT_ROOT/data/panel/holdout" ] || [ -e "\$ALPHASIEVE_HOT_ROOT/data/panel/fresh" ]; then
  echo "refusing: holdout or fresh data found on the remote root"; exit 3
fi
mkdir -p ${remote_root}/runs/${job_id}
"\$venv/bin/alphasieve" ${args} --json | tee ${remote_root}/runs/${job_id}/result.json
EOF
)
encoded=$(printf '%s' "$script" | base64 -w0)
ray job submit --submission-id "$job_id" --runtime-env-json "$runtime_env" -- bash -c "echo $encoded | base64 -d | bash -l"
echo "job ${job_id}; outputs in ${remote_root}/runs/${job_id}"
