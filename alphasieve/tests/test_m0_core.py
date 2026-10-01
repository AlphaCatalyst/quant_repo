import json
import sqlite3

import pytest
from pydantic import ValidationError

from alphasieve import __version__
from alphasieve.artifacts import artifact_id_for, write_artifact
from alphasieve.cli.main import main
from alphasieve.config import ensure_storage, get_settings, load_config
from alphasieve.contracts import FactorSpec, TrialLedgerEntry, export_schemas
from alphasieve.errors import AlphaSieveError
from alphasieve.ledger import append_trial, ledger_stats, verify_ledger
from alphasieve.state import connect


def run_cli(capsys, *argv) -> tuple[int, dict]:
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def entry(trial_id: str, kind: str, **kw) -> TrialLedgerEntry:
    return TrialLedgerEntry(trial_id=trial_id, record_kind=kind, created_by="agent", **kw)


def test_version_envelope(settings, capsys):
    code, out = run_cli(capsys, "version")
    assert code == 0
    assert out["schema"] == "alphasieve.response/v1"
    assert out["data"] == {"version": __version__}
    assert out["error"] is None


def test_init_creates_storage(settings, capsys):
    code, out = run_cli(capsys, "init")
    assert code == 0
    assert settings.state_db.exists()
    assert settings.artifacts_dir.exists()
    assert out["data"]["config_versions"]["splits"] == 1


def test_agent_role_denied_and_audited(settings, capsys, monkeypatch):
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    code, out = run_cli(capsys, "init")
    assert code == 4
    assert out["error"]["code"] == "PERMISSION_DENIED"
    conn = connect(get_settings().state_db)
    row = conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    assert row["command"] == "init" and row["status"] == "PERMISSION_DENIED" and row["role"] == "agent"


def test_invalid_role_rejected(settings, capsys, monkeypatch):
    monkeypatch.setenv("ALPHASIEVE_ROLE", "root")
    code, out = run_cli(capsys, "version")
    assert code == 2


def test_ledger_hash_chain_and_append_only(settings):
    conn = connect(settings.state_db)
    append_trial(conn, entry("t1", "started", factor_id="F-1", version=1, candidate_hash="h1"))
    append_trial(conn, entry("t1", "completed", factor_id="F-1", version=1, candidate_hash="h1",
                             outcome="robust_passed", metrics={"ic": 0.03}))
    report = verify_ledger(conn)
    assert report["ok"] and report["rows"] == 2 and report["open_trials"] == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE trials SET outcome = 'x' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM trials WHERE seq = 1")


def test_ledger_detects_tampering(settings):
    conn = connect(settings.state_db)
    append_trial(conn, entry("t1", "started"))
    append_trial(conn, entry("t1", "completed", metrics={"ic": 0.01}))
    conn.execute("DROP TRIGGER trials_no_update")
    conn.execute("UPDATE trials SET metrics_json = '{\"ic\":0.9}' WHERE seq = 2")
    report = verify_ledger(conn)
    assert not report["ok"]
    assert any(e.get("error") == "hash mismatch" for e in report["errors"])


def test_ledger_pairing_checks(settings):
    conn = connect(settings.state_db)
    append_trial(conn, entry("open", "started"))
    append_trial(conn, entry("dup", "started"))
    append_trial(conn, entry("dup", "started"))
    report = verify_ledger(conn)
    assert set(report["open_trials"]) == {"open", "dup"}
    assert any("expected one started" in e["error"] for e in report["errors"])


def test_ledger_stats_counts_repeats_and_failures(settings):
    conn = connect(settings.state_db)
    gate = {"l0": {"passed": True, "checks": []},
            "l1": {"passed": False, "checks": [{"name": "icir", "passed": False}]}}
    for i in range(3):
        append_trial(conn, entry(f"t{i}", "started", candidate_hash="same"))
        append_trial(conn, entry(f"t{i}", "completed", candidate_hash="same", outcome="evaluation_failed",
                                 gate_results=gate))
    stats = ledger_stats(conn)
    assert stats["completed_trials"] == 3
    assert stats["distinct_candidates"] == 1
    assert stats["failure_reasons"] == {"l1.icir": 3}


def test_ledger_cli(settings, capsys):
    run_cli(capsys, "init")
    code, out = run_cli(capsys, "ledger", "verify")
    assert code == 0 and out["data"]["ok"]


def test_artifact_content_addressed(settings):
    ensure_storage(settings)
    manifest = {"kind": "factor_eval", "candidate_hash": "abc", "seed": 1}
    a = write_artifact(settings, manifest, metrics={"ic": 0.1})
    b = write_artifact(settings, dict(manifest), metrics={"ic": 0.1})
    c = write_artifact(settings, {**manifest, "seed": 2})
    assert a == b == artifact_id_for(manifest)
    assert a != c
    assert (settings.artifacts_dir / a / "metrics.json").exists()


def test_factor_spec_validation():
    spec = FactorSpec(name="rev_5d", expression="ts_sum(ret_1d, 5)", hypothesis="reversal",
                      cell={"domain": "price", "form": "reversal", "scale": "short"}, direction=-1)
    assert spec.horizon == 5
    with pytest.raises(ValidationError):
        FactorSpec(name="Bad Name", expression="x", hypothesis="h", cell={"domain": "a", "form": "b", "scale": "short"})
    with pytest.raises(ValidationError):
        FactorSpec(name="ok_name", expression="x", hypothesis="h", cell={"domain": "a", "form": "b", "scale": "short"},
                   unknown_field=1)


def test_schema_export(tmp_path):
    paths = export_schemas(tmp_path)
    assert {p.name for p in paths} >= {"FactorSpec.schema.json", "TrialLedgerEntry.schema.json"}


def test_configs_have_versions(settings):
    for name in ("splits", "gate_policy", "costs"):
        assert "version" in load_config(settings, name)


def test_store_mount_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(tmp_path / "hot"))
    monkeypatch.setenv("ALPHASIEVE_STORE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("ALPHASIEVE_STORE_MOUNT", str(tmp_path / "not_a_mount"))
    with pytest.raises(AlphaSieveError) as exc:
        ensure_storage(get_settings())
    assert exc.value.code == "STORAGE_UNAVAILABLE"


def test_concurrent_migrations_are_serialised(tmp_path):
    import threading

    from alphasieve.state import connect

    errors = []

    def open_db():
        try:
            connect(tmp_path / "race.db").close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=open_db) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_state_backup_verified_copy(settings, capsys):
    conn = connect(settings.state_db)
    append_trial(conn, entry("t1", "started"))
    append_trial(conn, entry("t1", "completed", metrics={"ic": 0.01}))
    code, out = run_cli(capsys, "state", "backup")
    assert code == 0
    copy = settings.backups_dir / out["data"]["path"].split("/")[-1]
    assert copy.exists() and copy.with_suffix(".json").exists()
    assert out["data"]["ledger_rows"] == 2
    restored = connect(copy)
    assert verify_ledger(restored)["ok"]
    assert restored.execute("SELECT hash FROM trials ORDER BY seq DESC LIMIT 1").fetchone()[0] \
        == out["data"]["ledger_head"]


def test_state_backup_rejects_tampered_ledger(settings):
    from alphasieve.state.backup import list_backups, take_backup

    conn = connect(settings.state_db)
    append_trial(conn, entry("t1", "started"))
    conn.execute("DROP TRIGGER trials_no_update")
    conn.execute("UPDATE trials SET created_by = 'x' WHERE seq = 1")
    with pytest.raises(AlphaSieveError):
        take_backup(conn, settings.backups_dir)
    assert list_backups(settings.backups_dir) == []
    assert not list(settings.backups_dir.glob("*.partial"))


def test_state_backup_retention(tmp_path):
    from alphasieve.state.backup import list_backups, prune_backups

    days = [f"202609{d:02d}" for d in range(1, 6)]
    for day in days:
        for hour in (1, 13):
            (tmp_path / f"alphasieve-{day}T{hour:02d}0000Z.db").write_bytes(b"x")
    removed = prune_backups(tmp_path, keep_recent=3, keep_daily=2)
    kept = [p.name for p in list_backups(tmp_path)]
    assert kept == ["alphasieve-20260904T130000Z.db", "alphasieve-20260905T010000Z.db",
                    "alphasieve-20260905T130000Z.db"]
    assert len(removed) == 7
    prune_backups(tmp_path, keep_recent=1, keep_daily=3)
    assert [p.name for p in list_backups(tmp_path)] == ["alphasieve-20260904T130000Z.db",
                                                       "alphasieve-20260905T130000Z.db"]
