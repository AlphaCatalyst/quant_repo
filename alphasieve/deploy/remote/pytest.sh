#!/usr/bin/env bash
# Run the current working tree's pytest suite on orbenchtest.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
host=orbenchtest
base='alphasieve-ci'
cd "$repo"
remote_ssh() {
  ssh -o BatchMode=yes "$host" "$@" 2> >(sed '/^authz success$/d' >&2)
}

list=$(mktemp)
trap 'rm -f "$list"' EXIT
git ls-files -z --cached --others --exclude-standard |
  python3 -c 'import os,sys
out=sys.stdout.buffer
for path in sys.stdin.buffer.read().split(b"\0"):
    if not path or path.startswith((b".venv/", b"node_modules/", b"web/dist/")):
        continue
    if os.path.isfile(path) or os.path.islink(path):
        out.write(path+b"\0")' > "$list"

# The staging directory is new on every invocation. This makes the second rsync's
# --delete-after remove files deleted from the local working tree, at any depth.
stage=$(remote_ssh "mkdir -p ~/$base/tree ~/$base/stages ~/.local/bin && mktemp -d ~/$base/stages/pytest.XXXXXXXX")
stage=${stage##*$'\n'} # Drop the host's optional authz success banner.
if [[ $stage != /*/alphasieve-ci/stages/pytest.* ]]; then
  echo "unexpected remote staging path: $stage" >&2
  exit 2
fi
cleanup() {
  remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || true
}
trap 'rm -f "$list"; cleanup' EXIT

rsync -a --from0 --files-from="$list" -e 'ssh -o BatchMode=yes' ./ "$host:$stage/" 2> >(sed '/^authz success$/d' >&2)
remote_ssh "rsync -a --delete-after '$stage/' ~/$base/tree/"

# uv is copied once, so the remote does not need a system package installation.
if ! remote_ssh 'test -x ~/.local/bin/uv'; then
  rsync -a -e 'ssh -o BatchMode=yes' "$(command -v uv)" "$host:~/.local/bin/uv" 2> >(sed '/^authz success$/d' >&2)
fi

quoted_args=
if (( $# )); then
  quoted_args=$(printf '%q ' "$@")
fi
remote_ssh "bash -s -- $quoted_args" <<'REMOTE'
set -euo pipefail
base="$HOME/alphasieve-ci"
tree="$base/tree"
cd "$tree"
export UV_INDEX_URL=https://mirrors.tencent.com/pypi/simple/
export UV_PROJECT_ENVIRONMENT="$base/venv"
export UV_CACHE_DIR="$base/uv-cache"
export PYTHONPATH=
lock_hash=$(sha256sum uv.lock | cut -d' ' -f1)
if [[ ! -x "$base/venv/bin/pytest" || ! -f "$base/lock.sha256" || $(cat "$base/lock.sha256") != "$lock_hash" ]]; then
  echo 'remote: syncing locked Python dependencies' >&2
  "$HOME/.local/bin/uv" sync --frozen --python /usr/bin/python3.12 >&2
  printf '%s\n' "$lock_hash" > "$base/lock.sha256"
fi

scratch=$(mktemp -d "$base/pytest-data.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
export ALPHASIEVE_HOT_ROOT="$scratch/hot"
export ALPHASIEVE_STORE_ROOT="$scratch/store"
export ALPHASIEVE_STORE_MOUNT=
export ALPHASIEVE_RUN_REALDATA=0
export ALPHASIEVE_RUN_NETWORK=0
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONDONTWRITEBYTECODE=1
# NumPy/BLAS otherwise starts a thread pool in every pytest worker.
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMBA_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
python3 - "$base/venv/bin/pytest" "$@" <<'PY'
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys
import time

pytest, *args = sys.argv[1:]
selection = [arg for arg in args if arg.startswith('tests/') or arg.endswith('.py')]
files = selection or [str(p) for p in sorted(Path('tests').glob('test_*.py'))]
# Each file already runs with -q; a second -q hides the summary line parsed below.
options = [arg for arg in args if arg not in selection and arg not in ('-q', '--quiet')]
workers = min(int(os.environ.get('ALPHASIEVE_PYTEST_WORKERS', '24')), len(files))
if workers < 1:
    sys.exit('no test files selected')
start = time.monotonic()

def run(item):
    env = os.environ.copy()
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', item)
    env['ALPHASIEVE_HOT_ROOT'] += '/' + safe
    env['ALPHASIEVE_STORE_ROOT'] += '/' + safe
    cmd = [pytest, '-q', '--tb=short', '-p', 'no:cacheprovider', *options, item]
    result = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    return item, result.returncode, result.stdout

print(f'remote: {len(files)} files, {workers} workers, Python 3.12', flush=True)
results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
    futures = [pool.submit(run, item) for item in files]
    for future in concurrent.futures.as_completed(futures):
        item, code, output = future.result()
        if code == 5 and ('no tests ran' in output or 'deselected' in output):
            code = 0  # An optional -k/-m filter selected no cases in this file.
        results.append((item, code, output))
        if code:
            print(f'\nFAIL {item} (exit {code})', flush=True)
        else:
            print('.', end='', flush=True)

totals = dict.fromkeys(('passed', 'skipped', 'failed', 'error', 'deselected'), 0)
for item, code, output in results:
    for count, kind in re.findall(r'(\d+) (passed|skipped|failed|errors?|deselected)',
                                  output.splitlines()[-1] if output.splitlines() else ''):
        kind = 'error' if kind == 'errors' else kind
        totals[kind] += int(count)
    if code:
        print(f'\n--- {item} ---\n{output.rstrip()}\n--- end {item} ---', flush=True)
print()
print('remote summary: ' + ', '.join(f'{value} {kind}' for kind, value in totals.items() if value)
      + f'; {time.monotonic()-start:.1f}s wall', flush=True)
sys.exit(max((code for _, code, _ in results), default=5) if any(code for _, code, _ in results)
         else (5 if totals['passed'] + totals['skipped'] == 0 else 0))
PY
REMOTE
