#!/usr/bin/env bash
# Submit a dev-only AlphaSieve CLI command or Python batch tool to Ray.
# Usage: submit.sh [--no-wait] [--submission-id ID] [--batch] NAME COMMAND [ARGS...]
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
no_wait="${ALPHASIEVE_NO_WAIT:-}"
batch=0
submission_id=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-wait) no_wait=1; shift ;;
    --batch) batch=1; shift ;;
    --submission-id) submission_id="${2:?missing submission ID}"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
name="${1:?job name}"; shift
[ "$#" -gt 0 ] || { echo 'missing command' >&2; exit 2; }
case "$name" in *[!a-zA-Z0-9_-]*|'') echo 'invalid job name' >&2; exit 2;; esac
export RAY_ADDRESS="${RAY_ADDRESS:-http://28.83.35.117:8081}"
remote_root="${ALPHASIEVE_REMOTE_ROOT:-/taijifs_zw35/r2/felixjjiang/alphasieve}"
job_id="${submission_id:-alphasieve-${name}-$(date +%Y%m%d-%H%M%S)-$$}"
case "$job_id" in *[!a-zA-Z0-9_-]*|'') echo 'invalid submission ID' >&2; exit 2;; esac
runtime_env=$(python3 -c 'import json,sys; print(json.dumps({"working_dir":sys.argv[1],"excludes":[".venv/**","frontend/node_modules/**",".pytest_cache/**",".ruff_cache/**","**/__pycache__/**","src/alphasieve/web/dist/**"]}))' "$here")
args=$(printf '%q ' "$@")
remote_q=$(printf '%q' "$remote_root")
job_q=$(printf '%q' "$job_id")
tar_q=$(printf '%q' "${ALPHASIEVE_BATCH_INPUT_TAR:-}")
pre_q=$(printf '%q' "${ALPHASIEVE_PRE:-true}")
script=$(cat <<SCRIPT
set -euo pipefail
export UV_INDEX_URL=https://mirrors.tencent.com/pypi/simple/ UV_LINK_MODE=copy
venv=/tmp/alphasieve-venv-\$(sha256sum pyproject.toml | cut -c1-12)
[ -x "\$venv/bin/python" ] || { uv venv -q -p 3.12 "\$venv" && uv pip install -q --python "\$venv/bin/python" -e '.[tracking]'; }
uv pip install -q --no-deps --reinstall-package alphasieve --python "\$venv/bin/python" -e .
remote_root=$remote_q
job_id=$job_q
export ALPHASIEVE_JOB_ID="\$job_id" ALPHASIEVE_HOT_ROOT="\$remote_root/hot" ALPHASIEVE_STORE_ROOT="\$remote_root/store"
export ALPHASIEVE_ROLE=system ALPHASIEVE_USER="ray:\$job_id" ALPHASIEVE_STORE_MOUNT=
export ALPHASIEVE_EVAL_POLL=${ALPHASIEVE_EVAL_POLL:-0.2}
if [ -e "\$ALPHASIEVE_HOT_ROOT/data/panel/holdout" ] || [ -e "\$ALPHASIEVE_HOT_ROOT/data/panel/fresh" ]; then
  echo 'refusing: holdout or fresh data found on remote root' >&2; exit 3
fi
if [ -r "\$remote_root/secrets/runlab.env" ]; then
  set -a; . "\$remote_root/secrets/runlab.env"; set +a
  export ALPHASIEVE_TRACKING=runlab WANDB_BASE_URL=\${WANDB_BASE_URL:-http://runlab.woa.com} WANDB_SILENT=true
  export ALPHASIEVE_RUNLAB_ENTITY=\${ALPHASIEVE_RUNLAB_ENTITY:-felixjjiang}
fi
pre=$pre_q
bash -c "\$pre"
input_tar=$tar_q
if [ -n "\$input_tar" ]; then
  test -f "\$input_tar" || { echo 'missing batch input tar' >&2; exit 4; }
  mkdir -p "\${input_tar%.tar}"
  tar -xf "\$input_tar" -C "\${input_tar%.tar}"
fi
mkdir -p "\$remote_root/runs/\$job_id"
if [ $batch -eq 1 ]; then
  "\$venv/bin/python" $args | tee "\$remote_root/runs/\$job_id/result.json"
else
  "\$venv/bin/alphasieve" $args --json | tee "\$remote_root/runs/\$job_id/result.json"
fi
SCRIPT
)
encoded=$(printf '%s' "$script" | base64 -w0)
extra=()
[ -n "${ALPHASIEVE_ENTRYPOINT_CPUS:-}" ] && extra+=(--entrypoint-num-cpus "$ALPHASIEVE_ENTRYPOINT_CPUS")
[ -n "$no_wait" ] && extra+=(--no-wait)
ray job submit --submission-id "$job_id" "${extra[@]}" --runtime-env-json "$runtime_env" -- \
  bash -c "echo $encoded | base64 -d | bash -l"
echo "job ${job_id}; outputs in ${remote_root}/runs/${job_id}"
