import json

import numpy as np
import pandas as pd
import pytest
import yaml

from alphasieve.config import PACKAGE_CONFIG_DIR, Settings
from alphasieve.contracts.forward import ForwardConfig
from alphasieve.data.fresh import load_asof
from alphasieve.fresh import daily as daily_module
from alphasieve.fresh.daily import run_day
from alphasieve.fresh.service import policy
from alphasieve.state import connect
from alphasieve.strategy.execution import simulate
from alphasieve.training.task import parse_task
from alphasieve.util import canonical_json, utcnow_iso


@pytest.mark.parametrize('gap', [None, 16])
def test_forward_daily_sealed_25_days(tmp_path, gap, monkeypatch):
    settings = Settings(tmp_path / 'hot', tmp_path / 'store', None, PACKAGE_CONFIG_DIR, 'system', 'test')
    conn = connect(settings.state_db)
    history = pd.bdate_range(end='2026-10-02', periods=1830)
    fresh = pd.bdate_range('2026-10-05', periods=25)
    dates = history.append(fresh)
    names = ['A', 'B', 'C']
    rows = []
    for t, day in enumerate(dates):
        for j, code in enumerate(names):
            close = 10 + .02*t + .002*j*t
            if gap is not None and t == len(history) + gap and code == 'C':
                close = np.nan
            rows.append({'event_date': str(day.date()), 'observed_at': f'{day.date()}T17:00:00+08:00',
                         'code': code, 'open': close, 'close': close + .01, 'x': j + t*.01,
                         'industry': 'one', 'in_universe': True, 'in_zz500': True,
                         'tradable_buy': not (t == len(history) + 8 and code == 'C'),
                         'tradable_sell': not (t == len(history) + 8 and code == 'C'), 'is_st': False,
                         'is_suspended': False, 'circ_mv': 1e9 + j*1e8,
                         'amount': 1e8, 'ret_1d': .002})
    raw = tmp_path / 'raw.parquet'
    pd.DataFrame(rows).to_parquet(raw, index=False)
    bench = tmp_path / 'bench.parquet'
    pd.DataFrame({'event_date': [str(d.date()) for d in dates],
                  'observed_at': [f'{d.date()}T17:00:00+08:00' for d in dates],
                  'csi500_pit_float_cap_total_return_proxy_close': 1000 + np.arange(len(dates))}).to_parquet(
                      bench, index=False)
    task_data = yaml.safe_load((PACKAGE_CONFIG_DIR / 'training_tasks' / 'a_csi500_residual_v1.yaml').read_text())
    task_data['label']['horizons'] = [1]
    task_data['ensemble']['horizon_weights'] = {1: 1.0}
    task_data['label']['neutralize'] = []
    task_data['sample']['min_train_rows'] = 20
    task_data['sample']['min_names_per_date'] = 3
    task_data['sample']['train_stride'] = 1
    task_data['sample']['purge_days'] = 2
    task_data['features']['factor_refs'] = []
    task_data['features']['panel_fields'] = ['x']
    task_data['features']['preprocess'] = 'rank_zscore'
    task_data['portfolio']['rebalance_every'] = 5
    task_data['portfolio']['name_cap'] = .5
    task_data['portfolio']['industry_dev'] = .5
    task = parse_task(task_data)
    pol, pol_hash = policy(settings)
    pol['strategy_a']['valid_return_days_min'] = 20
    cfg = ForwardConfig(object_kind='strategy', mode='diagnostic_shadow', trial_id='S-synthetic',
                        source_hash='s'*64, bundle={'task': task.model_dump(mode='json'),
                        'features': {'factors': [], 'panel_fields': ['x']}}, feature_names=['x'],
                        chosen={'1': {'family': 'ridge', 'params': {'alpha': 1.0}}}, seeds=[0],
                        horizons=[1], primary_horizon=1, train_window='rolling', train_years=5,
                        purge_days=2, retrain='monthly', benchmark=pol['strategy_a']['benchmark'],
                        capital=1e6, start_date=str(fresh[0].date()))
    cid = 'F-synthetic'
    conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
                 ('D-synthetic', 'forward_request', 'FR-synthetic', 'approved', 'synthetic', 'test',
                  utcnow_iso(), 's'*64))
    conn.execute('INSERT INTO fresh_cohorts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (cid, 'strategy', 'diagnostic_shadow', 'S-synthetic', cfg.source_hash,
                  canonical_json(cfg.model_dump(mode='json')), cfg.digest, canonical_json(pol), pol_hash,
                  cfg.start_date, 'D-synthetic', 'test', utcnow_iso(), None))
    conn.execute('INSERT INTO paper_books VALUES (?,?,?,?,?,?,?,?)',
                 (f'B-{cid}', cid, 'observation', None, 1e6, cfg.benchmark, cfg.start_date, utcnow_iso()))
    calendar = [str(d.date()) for d in fresh]
    first_hash = None
    for i, day in enumerate(fresh):
        asof = str(day.date())
        if i == 10:
            original = daily_module._score_and_target
            def interrupted(*args, **kwargs):
                raise ValueError('synthetic interruption')
            monkeypatch.setattr(daily_module, '_score_and_target', interrupted)
        result = run_day(settings, conn, dict(conn.execute('SELECT * FROM fresh_cohorts').fetchone()), asof,
                         cutoff=f'{asof}T18:30:00+08:00', calendar=calendar,
                         sources={'panel': raw, 'benchmark': bench}, sealed_at=f'{asof}T18:00:00+08:00')
        if i == 10:
            assert result['status'] == 'blocked'
            revised = pd.read_parquet(raw)
            revised.loc[(revised.event_date == calendar[0]) & (revised.code == 'A'), 'close'] = 99.0
            revised.to_parquet(raw, index=False)
            monkeypatch.setattr(daily_module, '_score_and_target', original)
            result = run_day(settings, conn, dict(conn.execute('SELECT * FROM fresh_cohorts').fetchone()), asof,
                             cutoff=f'{asof}T18:30:00+08:00', calendar=calendar,
                             sources={'panel': raw, 'benchmark': bench}, sealed_at=f'{asof}T18:00:00+08:00')
        assert result['status'] == 'complete', str(result)
        if i == 0:
            first_hash = conn.execute('SELECT row_hash FROM fresh_days ORDER BY date LIMIT 1').fetchone()[0]
        if i == 12:
            revised = pd.read_parquet(raw)
            revised.loc[(revised.event_date == calendar[0]) & (revised.code == 'A'), 'close'] = 999.0
            revised.to_parquet(raw, index=False)
        retry = run_day(settings, conn, dict(conn.execute('SELECT * FROM fresh_cohorts').fetchone()), asof)
        assert retry['run_id'] == result['run_id']
        expected = 1 if i >= (22 if gap is not None else 20) else 0
        assert conn.execute('SELECT COUNT(*) FROM fresh_verdicts').fetchone()[0] == expected
    assert conn.execute('SELECT COUNT(*) FROM fresh_days').fetchone()[0] == 25
    assert conn.execute('SELECT COUNT(*) FROM forward_runs WHERE status="complete"').fetchone()[0] == 25
    assert conn.execute('SELECT COUNT(*) FROM model_snapshots').fetchone()[0] >= 2
    assert conn.execute('SELECT row_hash FROM fresh_days ORDER BY date LIMIT 1').fetchone()[0] == first_hash
    assert conn.execute('SELECT verdict FROM fresh_verdicts').fetchone()[0] == 'shadow_complete'
    assert conn.execute('SELECT COUNT(*) FROM fresh_observations').fetchone()[0] >= 20
    if gap is not None:
        states = [r[0] for r in conn.execute('SELECT status FROM paper_days ORDER BY date')]
        assert 'data_gap' in states and 'recovered_multi_day' in states
    else:
        panel = load_asof(settings, conn, calendar[-1])
        targets = {pd.Timestamp(row['date']): json.loads(row['target_json']) for row in
                   conn.execute('SELECT date,target_json FROM paper_targets ORDER BY date')}
        weights = pd.DataFrame.from_dict(targets, orient='index').reindex(columns=panel.codes).fillna(0.0)
        batch = simulate(weights, panel, benchmark=cfg.benchmark, aum=1e6)
        paper = pd.Series({pd.Timestamp(r['date']): r['nav'] for r in
                           conn.execute('SELECT date,nav FROM paper_days ORDER BY date')})
        np.testing.assert_allclose(paper.reindex(batch['_nav'].index), batch['_nav'], atol=1e-10, rtol=0)
        costs = pd.Series({pd.Timestamp(r['date']): json.loads(r['metrics_json'])['cost_ratio'] for r in
                           conn.execute('SELECT date,metrics_json FROM paper_days ORDER BY date')})
        np.testing.assert_allclose(costs.reindex(batch['_daily'].index), batch['_daily']['cost'],
                                   atol=1e-10, rtol=0)
        details = [json.loads(r['metrics_json']) for r in
                   conn.execute('SELECT metrics_json FROM paper_days ORDER BY date')]
        assert sum(len(d['fills']) for d in details) == conn.execute(
            'SELECT COUNT(*) FROM paper_fills').fetchone()[0]
        np.testing.assert_allclose(sum(d['actual_turnover'] for d in details) * 252 / len(details),
                                   batch['annual_turnover'], atol=1e-10, rtol=0)
        np.testing.assert_allclose(sum(d['costs']['impact'] / d['open_nav'] for d in details)
                                   * 252 / len(details), batch['annual_impact_cost'], atol=1e-10, rtol=0)
    halt = conn.execute('SELECT metrics_json FROM paper_days WHERE date=?', (calendar[8],)).fetchone()
    assert 'C' in json.loads(halt[0])['stale_codes']
