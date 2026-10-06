"""The minute tick isolates step failures and rejects overlapping runs."""

from types import SimpleNamespace

from alphasieve.cli import commands_control
from alphasieve.state.db import connect


def test_tick_records_step_error_and_continues(tmp_path, monkeypatch):
    conn = connect(tmp_path / "state.db")
    settings = SimpleNamespace(hot_root=tmp_path, role="system", user="test")
    ctx = SimpleNamespace(settings=settings, conn=conn)
    calls = []

    def broken_schedule(*args):
        raise RuntimeError("schedule unavailable")

    monkeypatch.setattr(commands_control.scheduler, "run_due", broken_schedule)
    monkeypatch.setattr(commands_control.jobs, "reconcile",
                        lambda *args: calls.append("jobs") or {"checked": 0})
    from alphasieve.control import health, resources

    monkeypatch.setattr(resources, "write_snapshot", lambda *args: calls.append("resources") or {})
    monkeypatch.setattr(health, "system_alerts", lambda *args: [])
    result = commands_control.cmd_control_tick(SimpleNamespace(), ctx).data
    assert result["errors"][0]["step"] == "schedule"
    assert calls == ["jobs", "resources"]
    assert result["jobs"] == {"checked": 0}


def test_tick_skips_when_lock_held(tmp_path):
    import fcntl

    conn = connect(tmp_path / "state.db")
    settings = SimpleNamespace(hot_root=tmp_path, role="system", user="test")
    ctx = SimpleNamespace(settings=settings, conn=conn)
    lock = tmp_path / "control" / "tick.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = commands_control.cmd_control_tick(SimpleNamespace(), ctx).data
    assert result == {"skipped": "overlapping tick"}
