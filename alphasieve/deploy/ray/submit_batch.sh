#!/usr/bin/env bash
# Submit a dev-only Python batch tool. The caller validates all data and stages
# any required dev-only input before invoking this script.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
name="${1:?job name}"; shift
[ "$#" -gt 0 ] || { echo 'usage: submit_batch.sh NAME command [args...]' >&2; exit 2; }
case "$name" in *[!a-zA-Z0-9_-]*|'') echo 'invalid job name' >&2; exit 2;; esac
export RAY_ADDRESS="${RAY_ADDRESS:-http://28.83.35.117:8081}"
remote_root="${ALPHASIEVE_REMOTE_ROOT:-/taijifs_zw35/r2/felixjjiang/alphasieve}"
job_id="alphasieve-${name}-$(date +%Y%m%d-%H%M%S)-$$"
runtime_env=$(python3 -c 'import json,sys; print(json.dumps({"working_dir":sys.argv[1],"excludes":[".venv/**","frontend/node_modules/**",".pytest_cache/**",".ruff_cache/**","**/__pycache__/**","src/alphasieve/web/dist/**"]}))' "$here")
args=$(printf '%q ' "$@")
remote_q=$(printf '%q' "$remote_root")
job_q=$(printf '%q' "$job_id")
tar_q=$(printf '%q' "${ALPHASIEVE_BATCH_INPUT_TAR:-}")
script=$(cat <<SCRIPT
set -euo pipefail
export UV_INDEX_URL=https://mirrors.tencent.com/pypi/simple/ UV_LINK_MODE=copy
venv=/tmp/alphasieve-venv-\$(sha256sum pyproject.toml | cut -c1-12)
[ -x "\$venv/bin/python" ] || { uv venv -q -p 3.12 "\$venv" && uv pip install -q --python "\$venv/bin/python" -e '.[tracking]'; }
uv pip install -q --no-deps --reinstall-package alphasieve --python "\$venv/bin/python" -e .
remote_root=$remote_q
job_id=$job_q
export ALPHASIEVE_JOB_ID="\$job_id" ALPHASIEVE_HOT_ROOT="\$remote_root/hot" ALPHASIEVE_STORE_ROOT="\$remote_root/store"
if [ -e "\$ALPHASIEVE_HOT_ROOT/data/panel/holdout" ] || [ -e "\$ALPHASIEVE_HOT_ROOT/data/panel/fresh" ]; then
  echo 'refusing: holdout or fresh data found on the remote root' >&2; exit 3
fi
input_tar=$tar_q
if [ -n "\$input_tar" ]; then
  test -f "\$input_tar" || { echo 'missing batch input tar' >&2; exit 4; }
  mkdir -p "\${input_tar%.tar}"
  tar -xf "\$input_tar" -C "\${input_tar%.tar}"
fi
mkdir -p "\$remote_root/runs/\$job_id"
"\$venv/bin/python" $args | tee "\$remote_root/runs/\$job_id/result.json"
SCRIPT
)
encoded=$(printf '%s' "$script" | base64 -w0)
extra=()
[ -n "${ALPHASIEVE_ENTRYPOINT_CPUS:-}" ] && extra+=(--entrypoint-num-cpus "$ALPHASIEVE_ENTRYPOINT_CPUS")
[ -n "${ALPHASIEVE_NO_WAIT:-}" ] && extra+=(--no-wait)
ray job submit --submission-id "$job_id" "${extra[@]}" --runtime-env-json "$runtime_env" -- \
  bash -c "echo $encoded | base64 -d | bash -l"
echo "job ${job_id}; outputs in ${remote_root}/runs/${job_id}"
