import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pytest

from alphasieve.config import PACKAGE_CONFIG_DIR, Settings
from alphasieve.contracts.forward import ForwardConfig
from alphasieve.errors import AlphaSieveError
from alphasieve.fresh.service import active_cohorts, approve_cohort, read_model
from alphasieve.state import connect


def _config():
    return ForwardConfig(object_kind='strategy', mode='diagnostic_shadow', trial_id='S-synthetic',
        source_hash='source', bundle={'features': {}}, feature_names=['x'],
        chosen={'1': {'family': 'ridge', 'params': {}}},
        seeds=[1], horizons=[1], primary_horizon=1, train_window='rolling', train_years=5,
        purge_days=2, retrain='monthly', benchmark='zz500', capital=5e8,
        start_date=(date.today() + timedelta(days=5)).isoformat())


def _settings(tmp_path):
    return Settings(hot_root=tmp_path/'hot', store_root=tmp_path/'store', store_mount=None,
                    config_dir=PACKAGE_CONFIG_DIR, role='human', user='synthetic-human')


def test_human_approval_is_explicit_and_immutable(tmp_path):
    settings = _settings(tmp_path)
    conn = connect(settings.state_db)
    cfg = _config()
    assert active_cohorts(conn, cfg.start_date) == []
    with pytest.raises(AlphaSieveError):
        approve_cohort(conn, settings, cfg, 'synthetic approval', approve_policy=False,
                       approve_operational_refit=True)
    with pytest.raises(AlphaSieveError):
        approve_cohort(conn, replace(settings, role='system'), cfg, 'synthetic approval',
                       approve_policy=True, approve_operational_refit=True)
    result = approve_cohort(conn, settings, cfg, 'synthetic approval', approve_policy=True,
                            approve_operational_refit=True)
    assert result['promotion_eligible'] is False
    assert len(active_cohorts(conn, cfg.start_date)) == 1
    assert read_model(conn)['cohorts'][0]['required_days'] == 120
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute('UPDATE fresh_cohorts SET config_hash=? WHERE cohort_id=?', ('tampered', result['cohort_id']))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute('DELETE FROM forward_ledger')
    assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM holdout_requests").fetchone()[0] == 0


def test_validation_requires_evidence_and_score_source_rejected(tmp_path):
    settings = _settings(tmp_path)
    conn = connect(settings.state_db)
    cfg = _config().model_copy(update={'mode': 'validation'})
    with pytest.raises(AlphaSieveError):
        approve_cohort(conn, settings, cfg, 'synthetic', approve_policy=True,
                       approve_operational_refit=True)
    cfg = _config().model_copy(update={'bundle': {'features': {'score_source': {'trial_id': 'S-old'}}}})
    with pytest.raises(AlphaSieveError):
        approve_cohort(conn, settings, cfg, 'synthetic', approve_policy=True,
                       approve_operational_refit=True)


def test_approval_commands_are_human_only():
    from alphasieve.cli.main import build_parser
    from alphasieve.cli.registry import COMMANDS

    build_parser()
    for name in ('fresh register', 'fresh pause', 'fresh close', 'paper approve'):
        assert COMMANDS[name].roles == ('human',)
    assert COMMANDS['fresh daily'].roles == ('system',)
