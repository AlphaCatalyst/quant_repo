"""Public system health checks and condition-keyed alerts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, date, datetime

from alphasieve.control import resources
from alphasieve.control.targets import load_targets
from alphasieve.util import canonical_json


def _check(check_id, status, title, detail, *, since=None, evidence=()):
    return {'id': check_id, 'status': status, 'title': title, 'detail': detail,
            'since': since, 'evidence': list(evidence)}


def _snapshot_date(conn, name):
    rows = conn.execute('SELECT params_json FROM data_snapshots WHERE dataset=? ORDER BY fetched_at DESC LIMIT 30',
                        (name,)).fetchall()
    dates = []
    for row in rows:
        params = json.loads(row[0])
        dates += [str(params[k])[:10] for k in ('date', 'end') if params.get(k)]
    return max(dates, default=None)


def _daily(settings, conn, latest):
    outcome = None
    try:
        from alphasieve.control.jobs import list_jobs
        jobs = list_jobs(conn, kind='local_command', limit=100)
        outcome = next((j for j in jobs if 'daily-update' in canonical_json(j.get('params', {}))), None)
    except Exception:
        pass
    if outcome:
        stamp = outcome.get('finished_at') or outcome.get('submitted_at')
        duration = None
        if outcome.get('finished_at') and outcome.get('submitted_at'):
            duration = int((datetime.fromisoformat(outcome['finished_at']) -
                            datetime.fromisoformat(outcome['submitted_at'])).total_seconds())
        elif outcome.get('submitted_at'):
            duration = int((datetime.now(UTC) - datetime.fromisoformat(outcome['submitted_at'])).total_seconds())
        if outcome['status'] in ('queued', 'submitted', 'running'):
            status = 'warn' if duration is not None and duration > 7200 else 'ok'
        else:
            status = 'ok' if outcome['status'] == 'succeeded' and stamp and stamp[:10] >= latest else 'fail'
        return _check('daily_update', status, '每日更新',
                      f"{outcome['status']}；耗时 {duration if duration is not None else '未知'} 秒",
                      since=stamp, evidence=[f"job:{outcome['job_id']}"])
    log = settings.hot_root / 'logs' / 'daily-update.jsonl'
    try:
        lines = log.read_text().splitlines()[-20:]
        rows = [json.loads(line) for line in lines]
        row = next((r for r in reversed(rows) if isinstance(r, dict) and r.get('command') == 'data daily-update'), None)
        if row:
            at = row.get('at') or row.get('timestamp') or datetime.fromtimestamp(log.stat().st_mtime, UTC).isoformat()
            state = row.get('status', 'unknown')
            warns = row.get('warnings', [])
            status = 'ok' if state == 'ok' and at[:10] >= latest else 'fail'
            return _check('daily_update', status, '每日更新',
                          f'{state}；警告 {len(warns)} 条；耗时 {row.get("duration_seconds", "未知")} 秒',
                          since=at, evidence=[str(log)])
    except (OSError, ValueError):
        pass
    return _check('daily_update', 'warn', '每日更新', '没有可核验的最近运行记录')


def _calendar_day(settings):
    try:
        from alphasieve.data.sync import latest_trading_day
        return latest_trading_day(settings)
    except Exception:
        # A missing calendar is evidence unavailable, never infer holiday staleness from weekdays.
        return None


def _release_check(settings):
    current = settings.hot_root / 'deploy' / 'current'
    try:
        deployed = subprocess.run(['git', '-C', str(current), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, timeout=2, check=True).stdout.strip()
        # Releases are worktrees; the first worktree entry is the development checkout.
        listing = subprocess.run(['git', '-C', str(current), 'worktree', 'list', '--porcelain'],
                                 capture_output=True, text=True, timeout=2, check=True).stdout
        head = next(line.split()[1] for line in listing.splitlines() if line.startswith('HEAD '))
        return _check('release', 'ok' if deployed == head else 'warn', '发布版本',
                      f'已部署 {deployed[:12]}；仓库 HEAD {head[:12]}', evidence=[deployed, head])
    except (OSError, subprocess.SubprocessError, StopIteration):
        return _check('release', 'warn', '发布版本', '未找到可核验的发布目录')


def _evalbridge(settings, snap):
    unit = snap.get('local', {}).get('units', {}).get('alphasieve-evalbridge.service', {})
    queue = settings.state_db.parent / 'evalq'
    now = datetime.now(UTC).timestamp()
    workers = 0
    for path in (queue / 'workers').glob('bridge-*.json'):
        try:
            if now - path.stat().st_mtime < 90:
                workers += int(json.loads(path.read_text()).get('remote_workers', 0))
        except (OSError, ValueError):
            continue
    pending = len(list((queue / 'pending').glob('*.json')))
    # The bridge withdraws its heartbeat while no remote worker is alive; only queued work makes that a problem.
    if unit.get('state') != 'active':
        status = 'fail'
    else:
        status = 'warn' if pending and not workers else 'ok'
    return _check('evalbridge', status, '评估桥接',
                  f"服务 {unit.get('state', '未知')}；远端 worker {workers} 个；待评估 {pending} 个")


def collect(settings, conn):
    checks = []
    trading = _calendar_day(settings)
    if trading:
        checks.append(_daily(settings, conn, trading))
        for dataset in ('daily', 'westock:margin', 'westock:fund_flow', 'cninfo_announcements',
                        'westock:cb_daily', 'sw_industry_hist'):
            try:
                latest = _snapshot_date(conn, dataset)
            except Exception:
                latest = None
            # Weekly SW history and fund flow need a looser reporting window.
            allowance = 7 if dataset == 'sw_industry_hist' else 2 if dataset == 'westock:fund_flow' else 0
            age = (date.fromisoformat(trading) - date.fromisoformat(latest)).days if latest else None
            status = 'warn' if age is None or age > allowance else 'ok'
            checks.append(_check(f'dataset:{dataset}', status, f'数据：{dataset}',
                                 f'最新 {latest or "未知"}；目标交易日 {trading}', since=latest))
    else:
        checks.append(_check('calendar', 'warn', '交易日历', '无法读取交易日历，跳过数据时效判定'))
    backups = sorted(settings.backups_dir.glob('alphasieve-*.db'))
    age = (datetime.now(UTC).timestamp() - backups[-1].stat().st_mtime) / 3600 if backups else None
    checks.append(_check('backup', 'ok' if age is not None and age < 2 else 'warn', '状态备份',
                         f'最近备份 {age:.1f} 小时前' if age is not None else '没有备份',
                         evidence=[str(backups[-1])] if backups else []))
    snap = resources.read_snapshot(settings)
    if snap is None:
        snap = resources.snapshot(settings)
    checks.append(_evalbridge(settings, snap))
    targets = load_targets(settings)
    for endpoint in targets.llm_endpoints:
        item = snap.get('llm_endpoints', {}).get(endpoint.name, {})
        paused = False
        if endpoint.name == 'codex-lb':
            try:
                from alphasieve.control.llm import state
                paused = bool(state(settings).get('paused_since'))
            except (OSError, ValueError):
                pass
        reachable = item.get('reachable') and not paused
        checks.append(_check(f'llm:{endpoint.name}', 'ok' if reachable else 'fail',
                             endpoint.name, '已暂停 agent 工作' if paused else
                             (f"可达；{item.get('latency_ms')} ms" if reachable
                              else item.get('error', '不可达'))))
    r2_up = 0
    for cluster in targets.ray_clusters:
        item = snap.get('ray_clusters', {}).get(cluster.name, {})
        if cluster.r2 and item.get('reachable'):
            r2_up += 1
        checks.append(_check(f'ray:{cluster.name}', 'ok' if item.get('reachable') else 'warn',
                             f'Ray {cluster.name}', f"存活节点 {item.get('nodes_alive', 0)}" if item.get('reachable')
                             else item.get('error', '不可达')))
    checks.append(_check('ray:r2', 'ok' if r2_up else 'fail', 'r2 集群', f'{r2_up} 个可达'))
    for host in targets.ssh_hosts:
        item = snap.get('ssh_hosts', {}).get(host.name, {})
        mounts = item.get('mounts', {})
        good = item.get('reachable') and all(mounts.values())
        checks.append(_check(f'ssh:{host.name}', 'ok' if good else 'fail', host.name,
                             f"挂载 {mounts}" if item.get('reachable') else item.get('error', '不可达')))
    try:
        from alphasieve.control.jobs import list_jobs
        failed = [j for j in list_jobs(conn, limit=100) if j['status'] == 'failed' and j.get('attempt', 0) >= 2]
    except Exception:
        failed = []
    checks.append(_check('jobs_repeated', 'warn' if failed else 'ok', '重复失败作业',
                         f'{len(failed)} 个', evidence=[f"job:{j['job_id']}" for j in failed]))
    units = snap.get('local', {}).get('units', {})
    failed_units = [name for name, unit in units.items() if unit.get('state') == 'failed']
    checks.append(_check('units_failed', 'fail' if failed_units else 'ok', '失败服务',
                         ', '.join(failed_units) if failed_units else '无'))
    disk = snap.get('local', {}).get('disks', {}).get('/data', {})
    free = disk.get('free_bytes', 0) / disk['total_bytes'] if disk.get('total_bytes') else None
    checks.append(_check('disk_data', 'warn' if free is None or free < .1 else 'ok', '/data 磁盘',
                         f'剩余 {free:.1%}' if free is not None else '无法读取'))
    checks.append(_release_check(settings))
    overall = 'fail' if any(c['status'] == 'fail' for c in checks) else ('warn' if any(
        c['status'] == 'warn' for c in checks) else 'ok')
    return {'checks': checks, 'overall': overall}


def _system_item(rule, subject, title, detail, severity, evidence, dedupe_key):
    fingerprint = dedupe_key or hashlib.sha256(canonical_json([title, detail, evidence]).encode()).hexdigest()[:16]
    key = f'system:{rule}|{subject}|{fingerprint}'
    return {'alert_id': hashlib.sha256(key.encode()).hexdigest()[:24], 'kind': 'system',
            'severity': severity, 'subject': subject, 'title': title, 'detail': detail,
            'evidence': list(evidence), 'created_at': datetime.now(UTC).isoformat(),
            'visibility': 'public'}


def system_alerts(settings, conn, asof):
    del asof
    checks = collect(settings, conn)['checks']
    return [_system_item(c['id'], c['id'], c['title'], c['detail'],
                         'critical' if c['status'] == 'fail' else 'warning', c['evidence'],
                         c['status'])
            for c in checks if c['status'] != 'ok']


def record_system_alert(settings, conn, *, rule, subject, title, detail, severity='warning', evidence=(),
                        dedupe_key=None):
    del settings
    item = _system_item(rule, subject, title, detail, severity, evidence, dedupe_key)
    cursor = conn.execute('INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,?,?,?)',
                          (item['alert_id'], item['kind'], item['severity'], item['subject'], item['title'],
                           item['detail'], canonical_json(item['evidence']), item['created_at'], 'public'))
    return bool(cursor.rowcount)
