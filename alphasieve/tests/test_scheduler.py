"""Schedule slot, missed-run and overlap behavior without starting systemd units."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from alphasieve.control import scheduler
from alphasieve.state import connect


def _fake_submit(settings, conn, kind, params, *, actor, idempotency_key):
    assert kind == "local_command" and actor == "system"
    job_id = f"J-test-{conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]}"
    conn.execute("INSERT INTO jobs(job_id,kind,idempotency_key,placement,attempt,max_attempts,status,"
                 "submitted_at,params_json) VALUES (?,?,?,?,?,?,?,?,?)",
                 (job_id, kind, idempotency_key, "local", 0, 3, "submitted",
                  datetime.now(UTC).isoformat(), "{}"))
    return {"job_id": job_id}


def test_slot_parsing_and_weekday_boundary():
    shanghai = scheduler.ZONE
    monday_morning = datetime(2026, 10, 5, 8, 0, tzinfo=shanghai)
    assert scheduler._due_at("daily 18:40 mon-sat", monday_morning) == datetime(
        2026, 10, 3, 18, 40, tzinfo=shanghai)
    assert scheduler._next_at("daily 18:40 mon-sat", monday_morning) == datetime(
        2026, 10, 5, 18, 40, tzinfo=shanghai)
    assert scheduler._due_at("hourly :00", monday_morning.replace(minute=42)).hour == 8
    assert scheduler._due_at("every 15m", monday_morning.replace(minute=42)).minute == 30


def test_first_slot_missed_slot_and_open_job(settings):
    with connect(settings.state_db) as conn:
        t = datetime(2026, 10, 6, 18, 41, tzinfo=scheduler.ZONE)
        first = scheduler.run_due(settings, conn, _fake_submit, now=t)
        assert [item["name"] for item in first["submitted"]] == ["daily-update"]
        assert {"name": "state-backup", "reason": "no_history_before_first_slot"} in first["skipped"]
        assert not first["errors"]
        backup = scheduler.run_due(settings, conn, _fake_submit, now=t.replace(hour=19, minute=1))
        assert [item["name"] for item in backup["submitted"]] == ["state-backup"]
        later = scheduler.run_due(settings, conn, _fake_submit, now=t.replace(hour=20, minute=1))
        assert later["submitted"] == []
        assert {"name": "state-backup", "reason": "previous_open"} in later["skipped"]
        conn.execute("UPDATE jobs SET status='succeeded'")
        resumed = scheduler.run_due(settings, conn, _fake_submit, now=t.replace(hour=20, minute=1))
        assert [item["name"] for item in resumed["submitted"]] == ["state-backup"]
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE idempotency_key LIKE 'schedule:state-backup:%'")
        assert count.fetchone()[0] == 2
        conn.execute("UPDATE jobs SET status='succeeded'")
        late = scheduler.run_due(settings, conn, _fake_submit, now=t + timedelta(days=1, minutes=50))
        assert "daily-update" in [item["name"] for item in late["submitted"]]


def test_schedule_env_reaches_systemd_run(settings, monkeypatch):
    from alphasieve.control.kinds import local_command

    captured = {}

    def fake_submit(settings, conn, kind, params, *, actor, idempotency_key):
        captured["params"] = params
        return _fake_submit(settings, conn, kind, params, actor=actor, idempotency_key=idempotency_key)

    with connect(settings.state_db) as conn:
        scheduler.run_due(settings, conn, fake_submit, now=datetime(2026, 10, 6, 18, 41, tzinfo=scheduler.ZONE))
    env = captured["params"]["env"]
    assert env["ALPHASIEVE_WESTOCK_CLI"].endswith("deploy/remote/westock-ssh")

    calls = []
    monkeypatch.setattr(local_command, "systemd_run",
                        lambda argv, timeout=10: calls.append(argv) or type("R", (), {"returncode": 0})())
    kind = local_command.LocalCommand()
    params, _ = kind.prepare(settings, None, {"name": "x", "argv": ["version"], "env": env})
    kind.submit(SimpleNamespace(settings=settings), {"job_id": "J-env", "params": params}, 1)
    assert f"--setenv=ALPHASIEVE_WESTOCK_CLI={env['ALPHASIEVE_WESTOCK_CLI']}" in calls[0]


def test_schedule_projection_and_config(settings):
    with connect(settings.state_db) as conn:
        rows = scheduler.list_schedule(settings, conn)
    assert [row["name"] for row in rows] == ["daily-update", "state-backup", "forward-daily"]
    assert [row["enabled"] for row in rows] == [True, True, False]
    assert all(row["placement"] == "local" and row["last_run"] is None and row["next_due"] for row in rows)
    assert rows[0]["command"] == ["data", "daily-update", "--json"]
