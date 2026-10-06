import sqlite3
import subprocess
from dataclasses import replace
from datetime import date, timedelta

import pytest

from alphasieve.approvals import create_challenge, verify_records
from alphasieve.config import PACKAGE_CONFIG_DIR, Settings
from alphasieve.contracts.forward import ForwardConfig
from alphasieve.errors import AlphaSieveError
from alphasieve.fresh.service import (
    active_cohorts,
    approve_cohort,
    approve_paper,
    pause_or_close,
    policy,
    prepare_cohort,
    read_model,
)
from alphasieve.state import connect
from alphasieve.util import canonical_json, sha256_hex, utcnow_iso


def _config():
    return ForwardConfig(object_kind='strategy', mode='diagnostic_shadow', trial_id='S-synthetic',
        source_hash='source', bundle={'features': {}}, feature_names=['x'],
        chosen={'1': {'family': 'ridge', 'params': {}}},
        seeds=[1], horizons=[1], primary_horizon=1, train_window='rolling', train_years=5,
        purge_days=2, retrain='monthly', benchmark='csi500_pit_float_cap_total_return_proxy', capital=1e6,
        start_date=(date.today() + timedelta(days=5)).isoformat())


def _settings(tmp_path):
    config = tmp_path / 'config'
    (config / 'approvers').mkdir(parents=True)
    (config / 'forward').mkdir()
    (config / 'forward' / 'policy_v1.yaml').write_bytes(
        (PACKAGE_CONFIG_DIR / 'forward' / 'policy_v1.yaml').read_bytes())
    (config / 'approvals.yaml').write_text('approvals:\n  require_signature: true\n')
    key = tmp_path / 'human'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    (config / 'approvers' / 'allowed_signers').write_text(
        'human ' + key.with_suffix('.pub').read_text())
    return Settings(hot_root=tmp_path/'hot', store_root=tmp_path/'store', store_mount=None,
                    config_dir=config, role='human', user='synthetic-human')


def _signed_request(conn, settings, cfg, tmp_path):
    request = prepare_cohort(conn, settings, cfg)
    challenge = create_challenge(conn, settings, 'fresh_cohort', request['request_id'], 'approve',
                                 tmp_path / 'challenge.txt')
    subprocess.run(['ssh-keygen', '-Y', 'sign', '-n', 'alphasieve-approval', '-f',
                    str(tmp_path / 'human'), challenge['path']], check=True, capture_output=True)
    return request['request_id'], challenge['path'] + '.sig'


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
    request_id, signature = _signed_request(conn, settings, cfg, tmp_path)
    with pytest.raises(AlphaSieveError, match='signature'):
        approve_cohort(conn, settings, cfg, 'synthetic approval', approve_policy=True,
                       approve_operational_refit=True, request_id=request_id)
    result = approve_cohort(conn, settings, cfg, 'synthetic approval', approve_policy=True,
                            approve_operational_refit=True, request_id=request_id, signature=signature)
    assert verify_records(conn, settings) == []
    assert result['promotion_eligible'] is False
    assert len(active_cohorts(conn, cfg.start_date)) == 1
    challenge = create_challenge(conn, settings, 'fresh_state', result['cohort_id'], 'pause',
                                 tmp_path / 'pause.txt')
    subprocess.run(['ssh-keygen', '-Y', 'sign', '-n', 'alphasieve-approval', '-f',
                    str(tmp_path / 'human'), challenge['path']], check=True, capture_output=True)
    with pytest.raises(AlphaSieveError, match='signature'):
        pause_or_close(conn, settings, result['cohort_id'], 'paused', 'synthetic pause')
    pause_or_close(conn, settings, result['cohort_id'], 'paused', 'synthetic pause', challenge['path'] + '.sig')
    assert active_cohorts(conn, cfg.start_date) == []
    challenge = create_challenge(conn, settings, 'fresh_state', result['cohort_id'], 'resume',
                                 tmp_path / 'resume.txt')
    subprocess.run(['ssh-keygen', '-Y', 'sign', '-n', 'alphasieve-approval', '-f',
                    str(tmp_path / 'human'), challenge['path']], check=True, capture_output=True)
    pause_or_close(conn, settings, result['cohort_id'], 'resumed', 'synthetic resume', challenge['path'] + '.sig')
    assert len(active_cohorts(conn, cfg.start_date)) == 1
    assert verify_records(conn, settings) == []
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


def test_paper_approval_requires_signed_locked_evidence(tmp_path):
    settings = _settings(tmp_path)
    conn = connect(settings.state_db)
    cfg = _config().model_copy(update={'mode': 'validation'})
    pol, pol_hash = policy(settings)
    cohort_id = 'F-synthetic-validation'
    conn.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',
                 ('D-synthetic-validation', 'forward_request', 'FR-synthetic', 'approved',
                  'synthetic', 'synthetic-human', utcnow_iso(), 'e' * 64))
    conn.execute('INSERT INTO fresh_cohorts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (cohort_id, 'strategy', 'validation', cfg.trial_id, cfg.source_hash,
                  canonical_json(cfg.model_dump(mode='json')), cfg.digest, canonical_json(pol), pol_hash,
                  cfg.start_date, 'D-synthetic-validation', 'synthetic-human', utcnow_iso(), None))
    metrics = canonical_json({'verdict': 'fresh_supported', 'synthetic': True})
    conn.execute('INSERT INTO fresh_verdicts VALUES (?,?,?,?,?)',
                 (cohort_id, 'fresh_supported', metrics, pol_hash, utcnow_iso()))
    evidence_hash = sha256_hex(metrics)
    start = (date.today() + timedelta(days=10)).isoformat()
    with pytest.raises(AlphaSieveError, match='signature'):
        approve_paper(conn, settings, cohort_id, evidence_hash, 'synthetic', start)
    challenge = create_challenge(conn, settings, 'paper_book', cohort_id, 'approve',
                                 tmp_path / 'paper.txt')
    subprocess.run(['ssh-keygen', '-Y', 'sign', '-n', 'alphasieve-approval', '-f',
                    str(tmp_path / 'human'), challenge['path']], check=True, capture_output=True)
    approved = approve_paper(conn, settings, cohort_id, evidence_hash, 'synthetic', start,
                             challenge['path'] + '.sig')
    assert approved['start_date'] == start
    assert verify_records(conn, settings) == []
