"""Persistent job lifecycle, retry identity, and audit evidence."""

from types import SimpleNamespace

from alphasieve.control import jobs
from alphasieve.state.db import connect


class FakeKind:
    placement = "ray"
    need_r2 = False
    max_attempts = 3

    def __init__(self):
        self.polls = []
        self.submissions = []

    def submit(self, ctx, job, attempt):
        self.submissions.append((job["job_id"], job["trial_id"], attempt, ctx.target.name))
        return {"external_id": f"remote-{attempt}", "address": ctx.target.address}

    def poll(self, ctx, job, handle):
        return self.polls.pop(0)

    def collect(self, ctx, job):
        return {"done": True}

    def classify(self, detail):
        return "infra" if "node died" in str(detail) else "task"


def _setup(tmp_path, monkeypatch):
    conn = connect(tmp_path / "state.db")
    settings = SimpleNamespace(role="system", user="test")
    kind = FakeKind()
    monkeypatch.setattr(jobs, "_kind", lambda name: kind)
    clusters = [SimpleNamespace(name="a", address="http://a"),
                SimpleNamespace(name="b", address="http://b")]
    monkeypatch.setattr(jobs, "load_targets",
                        lambda settings: SimpleNamespace(clusters=lambda **kw: clusters))
    return conn, settings, kind


def test_infra_retry_keeps_job_and_trial_and_fails_over(tmp_path, monkeypatch):
    conn, settings, kind = _setup(tmp_path, monkeypatch)
    first = jobs.submit(settings, conn, "fake", {}, actor="system", trial_id="S-same")
    assert first["status"] == "submitted"
    kind.polls.append(("failed", {"message": "node died"}))
    jobs.reconcile(settings, conn)
    waiting = jobs.list_jobs(conn)[0]
    assert waiting["status"] == "retry_wait"
    assert waiting["failure_class"] == "infra"
    conn.execute("UPDATE jobs SET next_retry_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
                 (first["job_id"],))
    jobs.reconcile(settings, conn)
    kind.polls.append(("succeeded", {}))
    jobs.reconcile(settings, conn)
    final = jobs.list_jobs(conn)[0]
    assert final["status"] == "succeeded"
    assert final["attempt"] == 2
    assert kind.submissions == [(first["job_id"], "S-same", 1, "a"),
                                (first["job_id"], "S-same", 2, "b")]
    assert [r["outcome"] for r in conn.execute(
        "SELECT outcome FROM job_attempts WHERE job_id=? ORDER BY attempt", (first["job_id"],))] == [
            "failed", "succeeded"]
    assert [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM events WHERE object_id=? ORDER BY seq", (first["job_id"],))] == [
            "job_status", "job_status", "job_status", "job_status", "job_status"]


def test_idempotent_submission(tmp_path, monkeypatch):
    conn, settings, kind = _setup(tmp_path, monkeypatch)
    one = jobs.submit(settings, conn, "fake", {}, actor="system", idempotency_key="same")
    two = jobs.submit(settings, conn, "fake", {}, actor="system", idempotency_key="same")
    assert one["job_id"] == two["job_id"]
    assert len(kind.submissions) == 1
