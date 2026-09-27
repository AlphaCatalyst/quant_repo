"""Persistent evaluation service: a directory queue, long-lived workers that keep the dev panel in memory, and
a bridge that forwards jobs to a remote queue (taijifs) served by platform workers.

The ledger is never written here: ``evaluate_spec`` records the trial locally before submitting and finalises
it locally after the result comes back. Workers only compute dev-tier jobs.

Layout under a queue root: ``pending/``, ``running/``, ``done/``, ``workers/`` (heartbeats).
"""

import json
import os
import socket
import threading
import time
import traceback
import uuid
from pathlib import Path

from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError

HEARTBEAT_MAX_AGE = 60.0
DEFAULT_WAIT = 45 * 60.0


def local_queue_root(settings: Settings) -> Path:
    return settings.state_db.parent / "evalq"


class DirQueue:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        for sub in ("pending", "running", "done", "workers"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, payload: dict) -> None:
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def submit(self, job: dict) -> str:
        self._write(self.root / "pending" / f"{job['job_id']}.json", job)
        return job["job_id"]

    def claim(self) -> dict | None:
        def mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return float("inf")

        for path in sorted(self.root.glob("pending/*.json"), key=mtime):
            target = self.root / "running" / path.name
            try:
                os.rename(path, target)
            except (FileNotFoundError, OSError):
                continue
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                target.unlink(missing_ok=True)
        return None

    def complete(self, job_id: str, result: dict) -> None:
        self._write(self.root / "done" / f"{job_id}.json", result)
        (self.root / "running" / f"{job_id}.json").unlink(missing_ok=True)

    def result(self, job_id: str) -> dict | None:
        path = self.root / "done" / f"{job_id}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        path.unlink(missing_ok=True)
        return payload

    def wait(self, job_id: str, timeout: float = DEFAULT_WAIT, poll: float = 0.3,
             orphan_grace: float = 90.0) -> dict:
        deadline = time.monotonic() + timeout
        last_seen = time.monotonic()
        while time.monotonic() < deadline:
            result = self.result(job_id)
            if result is not None:
                return result
            if self.alive_workers():
                last_seen = time.monotonic()
            elif time.monotonic() - last_seen > orphan_grace:
                break
            time.sleep(poll)
        for sub in ("pending", "running"):
            (self.root / sub / f"{job_id}.json").unlink(missing_ok=True)
        raise AlphaSieveError("INTERNAL", f"evaluation job {job_id} got no result (timeout or no live worker)")

    def heartbeat(self, worker_id: str, info: dict) -> None:
        self._write(self.root / "workers" / f"{worker_id}.json", {**info, "ts": time.time()})

    def retire(self, worker_id: str) -> None:
        (self.root / "workers" / f"{worker_id}.json").unlink(missing_ok=True)

    def alive_workers(self, max_age: float = HEARTBEAT_MAX_AGE) -> list[dict]:
        out = []
        for path in self.root.glob("workers/*.json"):
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if time.time() - info.get("ts", 0) <= max_age:
                out.append({"worker_id": path.stem, **info})
        return out

    def depth(self) -> dict:
        return {sub: len(list(self.root.glob(f"{sub}/*.json"))) for sub in ("pending", "running", "done")}


def queue_executor(settings: Settings):
    """Executor for ``evaluate_spec`` when a live worker or bridge serves the local queue; otherwise None."""
    mode = os.environ.get("ALPHASIEVE_EVAL_QUEUE", "auto")
    if mode == "off":
        return None
    queue = DirQueue(local_queue_root(settings))
    if not queue.alive_workers():
        if mode == "require":
            raise AlphaSieveError("INTERNAL", "no live evaluation worker for the local queue")
        return None
    timeout = float(os.environ.get("ALPHASIEVE_EVAL_WAIT", DEFAULT_WAIT))

    def run(job: dict) -> dict:
        return queue.wait(queue.submit(job), timeout)

    return run


def run_worker(settings: Settings, queue_root: Path | str, max_jobs: int | None = None, idle_exit: float | None = None,
               poll: float | None = None, universes: tuple[str, ...] = ("csi800",)) -> dict:
    from alphasieve.data.access import load_panel
    from alphasieve.evaluation.evaluate import compute_job

    queue = DirQueue(queue_root)
    poll = poll if poll is not None else float(os.environ.get("ALPHASIEVE_EVAL_POLL", "0.2"))
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    info = {"host": socket.gethostname(), "pid": os.getpid(), "kind": "worker", "started": time.time()}
    status = {"state": "loading", "jobs": 0}
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(10):
            try:
                queue.heartbeat(worker_id, {**info, **status})
            except OSError:
                pass

    queue.heartbeat(worker_id, {**info, **status})
    threading.Thread(target=beat, daemon=True).start()
    panels = {u: load_panel(settings, "dev", role="system", universe=u) for u in universes}
    status.update(state="idle", panels={u: p.signature for u, p in panels.items()})
    done, last_job = 0, time.monotonic()
    try:
        while max_jobs is None or done < max_jobs:
            try:
                job = queue.claim()
            except OSError:
                job = None
            if job is None:
                if idle_exit is not None and time.monotonic() - last_job > idle_exit:
                    break
                time.sleep(poll)
                continue
            status.update(state="busy", job=job["job_id"])
            started = time.monotonic()
            try:
                universe = job["spec"].get("universe", "csi800")
                if universe not in panels:
                    panels[universe] = load_panel(settings, "dev", role="system", universe=universe)
                result = compute_job(settings, job, panel=panels[universe])
            except Exception as exc:  # noqa: BLE001
                result = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]}
            result["worker"] = {"id": worker_id, "seconds": round(time.monotonic() - started, 2)}
            queue.complete(job["job_id"], result)
            done += 1
            status.update(state="idle", job=None, jobs=done)
            last_job = time.monotonic()
    finally:
        stop.set()
        queue.retire(worker_id)
    return {"worker_id": worker_id, "jobs": done}


def run_bridge(local_root: Path | str, remote_root: Path | str, idle_exit: float | None = None,
               poll: float = 0.3) -> dict:
    """Forward local jobs to the remote queue and bring results back; advertise remote workers locally."""
    local, remote = DirQueue(local_root), DirQueue(remote_root)
    bridge_id = f"bridge-{socket.gethostname()}-{os.getpid()}"
    inflight: set[str] = set()
    seen: dict[str, float] = {}
    forwarded, last_activity, last_beat = 0, time.monotonic(), 0.0
    try:
        while True:
            if time.monotonic() - last_beat > 5:
                for w in remote.alive_workers():
                    seen[w["worker_id"]] = max(seen.get(w["worker_id"], 0.0), w["ts"])
                live = [k for k, ts in seen.items() if time.time() - ts <= HEARTBEAT_MAX_AGE]
                if live:
                    local.heartbeat(bridge_id, {"kind": "bridge", "remote": str(remote.root),
                                                "remote_workers": len(live)})
                else:
                    local.retire(bridge_id)
                last_beat = time.monotonic()
            live_now = any(time.time() - ts <= HEARTBEAT_MAX_AGE for ts in seen.values())
            job = local.claim() if live_now else None
            if job is not None:
                remote.submit(job)
                inflight.add(job["job_id"])
                forwarded += 1
                last_activity = time.monotonic()
            for job_id in list(inflight):
                result = remote.result(job_id)
                if result is not None:
                    local.complete(job_id, result)
                    inflight.discard(job_id)
                    last_activity = time.monotonic()
            if job is None:
                if idle_exit is not None and not inflight and time.monotonic() - last_activity > idle_exit:
                    break
                time.sleep(poll)
    finally:
        local.retire(bridge_id)
    return {"bridge_id": bridge_id, "forwarded": forwarded}
