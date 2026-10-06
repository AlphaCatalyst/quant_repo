#!/usr/bin/env bash
# Test an immutable commit on orbenchtest and atomically publish its worktree.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=${ALPHASIEVE_DEPLOY_ROOT:-/data/alphasieve/deploy}
hot=${ALPHASIEVE_HOT_ROOT:-/data/alphasieve}
web_url=${ALPHASIEVE_WEB_HEALTH_URL:-http://9.134.61.161:8720}
commit=HEAD
rollback=0
skip_tests=0
dry_run=0
for arg in "$@"; do
  case "$arg" in
    --rollback) rollback=1 ;;
    --skip-tests) skip_tests=1 ;;
    --dry-run) dry_run=1 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) commit=$arg ;;
  esac
done
cd "$repo"
if (( rollback )); then
  current=$(readlink -f "$root/current" || true)
  previous=$(python3 - "$hot/control/releases.jsonl" "$root/releases" "$current" <<'PY'
import json, pathlib, sys
log, releases, current = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
if log.exists():
    for line in reversed(log.read_text().splitlines()):
        try:
            candidate = releases / json.loads(line)['sha']
        except (ValueError, KeyError):
            continue
        if candidate.is_dir() and candidate != current:
            print(candidate)
            break
PY
)
  [[ -n $previous ]] || { echo 'no previous release' >&2; exit 1; }
  sha=$(basename "$previous")
else
  sha=$(git rev-parse --verify "${commit}^{commit}")
fi
if (( dry_run )); then
  echo "release commit: $sha"
  if (( rollback )); then echo "rollback to $root/releases/$sha";
  elif (( skip_tests )); then echo 'WARNING: tests explicitly skipped';
  else echo "test exact commit $sha via deploy/remote/pytest.sh"; fi
  echo "worktree $root/releases/$sha; uv sync --frozen; switch $root/current; restart alphasieve-web; health check; record release; keep 5"
  exit 0
fi
summary=rollback
if (( ! rollback )); then
  if (( skip_tests )); then
    echo 'WARNING: release tests explicitly skipped' >&2
    summary='skipped by explicit flag'
  else
    temp=$(mktemp -d)
    trap 'git worktree remove --force "$temp" >/dev/null 2>&1 || true' EXIT
    git worktree add --detach "$temp" "$sha" >&2
    output=$(mktemp)
    if "$temp/deploy/remote/pytest.sh" >"$output" 2>&1; then
      summary=$(grep 'remote summary:' "$output" | tail -1 || true)
      cat "$output"
    else
      cat "$output" >&2
      rm -f "$output"
      exit 1
    fi
    rm -f "$output"
    git worktree remove "$temp"
    trap - EXIT
  fi
  if [[ ! -d $root/releases/$sha ]]; then
    mkdir -p "$root/releases"
    git worktree add --detach "$root/releases/$sha" "$sha"
  fi
  UV_INDEX_URL=https://mirrors.tencent.com/pypi/simple/ uv sync --frozen --directory "$root/releases/$sha"
fi
old=$(readlink -f "$root/current" || true)
ln -sfn "$root/releases/$sha" "$root/.current-next"
mv -Tf "$root/.current-next" "$root/current"
wait_web() {
  for _ in {1..15}; do
    if curl -fsS --max-time 2 "$web_url/api/health" >/dev/null 2>&1 ||
       curl -fsS --max-time 2 "$web_url/" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}
if ! systemctl restart alphasieve-web || ! wait_web; then
  if [[ -n $old ]]; then ln -sfn "$old" "$root/.current-next"; mv -Tf "$root/.current-next" "$root/current"; systemctl restart alphasieve-web || true; fi
  echo 'web health check failed; previous symlink restored' >&2
  exit 1
fi
mkdir -p "$hot/control"
python3 - "$hot/control/releases.jsonl" "$sha" "$summary" "${SUDO_USER:-$(id -un)}" <<'PY'
import datetime, json, sys
with open(sys.argv[1], 'a', encoding='utf-8') as f:
    f.write(json.dumps({'sha': sys.argv[2], 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'by': sys.argv[4], 'tests': sys.argv[3]}, ensure_ascii=False) + '\n')
PY
mapfile -t old_releases < <(find "$root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2- | tail -n +6)
for path in "${old_releases[@]}"; do
  [[ $(readlink -f "$root/current") == "$path" ]] || git worktree remove "$path"
done
