"""SSH vendor relay and local concurrency boundaries."""

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alphasieve.data.providers import westock

WRAPPER = Path(__file__).resolve().parents[1] / "deploy/remote/westock-ssh"


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


def test_wrapper_quotes_arguments_and_discards_banner(tmp_path):
    ssh = _script(tmp_path / "ssh", 'echo "authz success" >&2\nbash -c "${@: -1}"\n')
    vendor = _script(tmp_path / "remote vendor", 'printf "%s\\n" "$@"\n')
    marker = tmp_path / "injected"
    args = ["a b", "single'quote", 'double"quote', f"$(touch {marker})", "semi;colon"]
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "ALPHASIEVE_WESTOCK_REMOTE_CLI": str(vendor), "ALPHASIEVE_WESTOCK_HOST": "fake"}
    result = subprocess.run([WRAPPER, *args], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == args
    assert result.stderr == ""
    assert ssh.exists()
    assert not marker.exists()


def test_wrapper_falls_back_on_255(tmp_path):
    _script(tmp_path / "ssh", 'echo "ssh: connect to host fake: Connection refused" >&2\nexit 255\n')
    local = _script(tmp_path / "local", 'printf "local:%s\\n" "$*"\n')
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "ALPHASIEVE_WESTOCK_LOCAL_CLI": str(local), "ALPHASIEVE_WESTOCK_HOST": "fake",
           "XDG_RUNTIME_DIR": str(tmp_path)}
    result = subprocess.run([WRAPPER, "a b"], env=env, capture_output=True, text=True, check=True)
    assert result.stdout == "local:a b\n"
    assert result.stderr.count("using local CLI") == 1


def test_wrapper_falls_back_on_ssh_error_line(tmp_path):
    _script(tmp_path / "ssh", 'echo "ssh: Could not resolve hostname fake" >&2\nexit 1\n')
    local = _script(tmp_path / "local", 'printf "[]\\n"\n')
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "ALPHASIEVE_WESTOCK_LOCAL_CLI": str(local), "ALPHASIEVE_WESTOCK_HOST": "fake",
           "XDG_RUNTIME_DIR": str(tmp_path)}
    result = subprocess.run([WRAPPER], env=env, capture_output=True, text=True, check=True)
    assert result.stdout == "[]\n"
    assert result.stderr.count("using local CLI") == 1


def test_empty_host_uses_local_without_ssh(tmp_path):
    _script(tmp_path / "ssh", 'exit 99\n')
    local = _script(tmp_path / "local", 'printf "local:%s\\n" "$*"\n')
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "ALPHASIEVE_WESTOCK_LOCAL_CLI": str(local), "ALPHASIEVE_WESTOCK_HOST": "",
           "XDG_RUNTIME_DIR": str(tmp_path)}
    result = subprocess.run([WRAPPER, "a b"], env=env, capture_output=True, text=True, check=True)
    assert result.stdout == "local:a b\n"
    assert result.stderr == ""


def test_provider_bounds_local_node_processes(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_run(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return subprocess.CompletedProcess(args, 0, json.dumps([]), "")

    monkeypatch.setattr(westock, "CLI", "/usr/local/bin/westock-data")
    monkeypatch.setattr(westock, "_LOCAL_SEMAPHORE", threading.BoundedSemaphore(3))
    monkeypatch.setattr(westock.subprocess, "run", fake_run)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: westock._call(["fund", "flow", "sh600000"]), range(24)))
    assert peak == 3
