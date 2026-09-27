import threading
from dataclasses import replace

import pytest
from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.config import get_settings
from alphasieve.contracts import FactorSpec
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation import service
from alphasieve.evaluation.evaluate import build_job, compute_job, evaluate_spec
from alphasieve.ledger import verify_ledger
from alphasieve.state import connect

REVERSAL = {"name": "rev_3d_excess", "expression": "ts_sum(excess_ret_1d, 3)", "direction": -1,
            "hypothesis": "short-term reversal", "cell": {"domain": "price", "form": "reversal", "scale": "short"}}
MOMENTUM = {"name": "mom_20d", "expression": "ts_sum(ret_1d, 20)", "direction": 1, "hypothesis": "momentum",
            "cell": {"domain": "price", "form": "change_momentum", "scale": "medium"}}


def start(target, **kwargs):
    thread = threading.Thread(target=target, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def wait_for_workers(queue, n=1, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len([w for w in queue.alive_workers() if w.get("state") != "loading" or w.get("kind") == "bridge"]) >= n:
            return
        time.sleep(0.2)
    raise AssertionError("worker did not start")


def test_dir_queue_roundtrip(tmp_path):
    q = service.DirQueue(tmp_path / "q")
    q.submit({"job_id": "a", "x": 1})
    job = q.claim()
    assert job == {"job_id": "a", "x": 1} and q.claim() is None
    q.complete("a", {"ok": True})
    assert q.result("a") == {"ok": True} and q.result("a") is None


def test_queue_matches_in_process_and_bridge(panel_settings, monkeypatch, tmp_path):
    start_campaign(panel_settings, campaign_spec("c-svc"), monkeypatch)
    conn = connect(panel_settings.state_db)
    human = replace(panel_settings, role="human")
    assert service.queue_executor(human) is None
    reference = evaluate_spec(human, conn, FactorSpec(**REVERSAL))

    local_root = service.local_queue_root(panel_settings)
    worker = start(service.run_worker, settings=panel_settings, queue_root=local_root, idle_exit=20)
    wait_for_workers(service.DirQueue(local_root))
    executor = service.queue_executor(human)
    assert executor is not None
    agent = replace(get_settings(), role="agent")
    via_queue = evaluate_spec(agent, conn, FactorSpec(**REVERSAL), campaign_id="c-svc", executor=executor)
    assert via_queue["worker"]["id"]
    assert via_queue["gates"] == reference["gates"] and via_queue["metrics"] == reference["metrics"]
    worker.join(timeout=60)

    remote_root = tmp_path / "remote-q"
    remote_worker = start(service.run_worker, settings=panel_settings, queue_root=remote_root, idle_exit=25)
    wait_for_workers(service.DirQueue(remote_root))
    bridge = start(service.run_bridge, local_root=local_root, remote_root=remote_root, idle_exit=10)
    wait_for_workers(service.DirQueue(local_root))
    bridged = evaluate_spec(agent, conn, FactorSpec(**MOMENTUM), campaign_id="c-svc",
                            executor=service.queue_executor(human))
    assert bridged["worker"]["id"] and bridged["outcome"] in ("evaluation_failed", "robust_passed", "robust_failed")
    bridge.join(timeout=60)
    remote_worker.join(timeout=60)
    assert verify_ledger(conn)["ok"]
    assert conn.execute("SELECT COUNT(*) FROM trials WHERE record_kind = 'completed'").fetchone()[0] == 3


def test_worker_refuses_non_dev_jobs(panel_settings):
    conn = connect(panel_settings.state_db)
    job = build_job(panel_settings, conn, FactorSpec(**REVERSAL), "ts_sum(excess_ret_1d,3)", "h", "holdout", {}, {})
    with pytest.raises(AlphaSieveError):
        compute_job(panel_settings, job)


def test_concurrent_claims_take_each_job_once(tmp_path):
    q = service.DirQueue(tmp_path / "race")
    for i in range(200):
        q.submit({"job_id": f"j{i:03d}"})
    taken, errors = [], []

    def grab():
        try:
            while (job := service.DirQueue(tmp_path / "race").claim()) is not None:
                taken.append(job["job_id"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(taken) == [f"j{i:03d}" for i in range(200)]
