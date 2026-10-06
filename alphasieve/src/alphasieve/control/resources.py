"""Bounded, read-only compute resource probes and seven-day history."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

from alphasieve.control.targets import load_targets


def _now():
    return datetime.now(UTC).isoformat()


def _http(url, timeout):
    with urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _memory():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            values[key] = int(value.split()[0]) * 1024
    return values


def _group(command, cgroup, scicomp_containers=()):
    s = (command + ' ' + cgroup).lower()
    if 'alphasieve' in s:
        return 'alphasieve'
    if 'scicomp-' in s or 'scicomp-foundry' in s or any(cid in cgroup for cid in scicomp_containers):
        return 'scicomp-foundry'
    if 'codex' in s or 'claude' in s:
        return 'codex/claude'
    if 'docker' in s or 'containerd' in s:
        return 'docker'
    return 'other'


def _local():
    mem = _memory()
    disks = {}
    for path in ('/', '/data'):
        if Path(path).exists():
            d = shutil.disk_usage(path)
            disks[path] = {'total_bytes': d.total, 'used_bytes': d.used, 'free_bytes': d.free}
    groups = {name: {'cpu_percent': 0.0, 'rss_bytes': 0, 'processes': 0}
              for name in ('alphasieve', 'scicomp-foundry', 'codex/claude', 'docker', 'other')}
    scicomp_containers = set()
    try:
        output = subprocess.run(['docker', 'ps', '--format', '{{.ID}} {{.Names}}'],
                                capture_output=True, text=True, timeout=2).stdout
        scicomp_containers = {parts[0] for line in output.splitlines()
                             if len(parts := line.split()) >= 2 and parts[1].startswith('scicomp-')}
    except (OSError, subprocess.TimeoutExpired):
        pass
    result = subprocess.run(['ps', '-eo', 'pid=,pcpu=,rss=,args='], capture_output=True,
                            text=True, timeout=3, check=True)
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, cpu, rss = int(parts[0]), float(parts[1]), int(parts[2])
        except ValueError:
            continue
        try:
            cgroup = Path(f'/proc/{pid}/cgroup').read_text()
        except OSError:
            cgroup = ''
        group = groups[_group(parts[3], cgroup, scicomp_containers)]
        group['cpu_percent'] += cpu
        group['rss_bytes'] += rss * 1024
        group['processes'] += 1
    units = {}
    try:
        output = subprocess.run(['systemctl', 'list-units', '--all', '--plain', '--no-legend',
                                 'alphasieve-*.service'], capture_output=True, text=True, timeout=3).stdout
        names = [line.split()[0] for line in output.splitlines() if line.split()]
        if names:
            props = subprocess.run(['systemctl', 'show', *names, '-p', 'Id', '-p', 'ActiveState',
                                    '-p', 'Result', '-p', 'ActiveEnterTimestamp', '-p',
                                    'InactiveEnterTimestamp'], capture_output=True, text=True, timeout=3).stdout
            for block in props.strip().split('\n\n'):
                p = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
                if p.get('Id'):
                    units[p['Id']] = {'state': p.get('ActiveState'), 'last_result': p.get('Result'),
                                      'last_start': p.get('ActiveEnterTimestamp'),
                                      'last_stop': p.get('InactiveEnterTimestamp')}
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'reachable': True, 'cpu_count': os.cpu_count(), 'loadavg': list(os.getloadavg()),
            'memory': mem, 'disks': disks, 'groups': groups, 'units': units}


def _ssh(host):
    script = """printf 'cores=%s\\n' "$(nproc)"; printf 'load='; cat /proc/loadavg;
awk '/^MemAvailable:/ {print "mem_available=" $2*1024}' /proc/meminfo;
for m in "$@"; do
 if mountpoint -q -- "$m"; then printf 'mount=%s:ok\\n' "$m";
 else printf 'mount=%s:missing\\n' "$m"; fi;
done;
if test -x "$HOME/.local/bin/westock-data" || command -v westock-data >/dev/null 2>&1;
then echo westock_data=present; else echo westock_data=missing; fi"""
    try:
        remote = 'bash -c ' + shlex.quote(script) + ' -- ' + ' '.join(shlex.quote(m) for m in host.mounts)
        proc = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host.alias,
                               remote], capture_output=True,
                              text=True, timeout=7)
        if proc.returncode:
            return {'reachable': False, 'error': proc.stderr.strip()[:200] or f'ssh exit {proc.returncode}'}
        lines = [line for line in proc.stdout.splitlines() if line.strip() != 'authz success']
        data = dict(line.split('=', 1) for line in lines if '=' in line)
        mount_lines = [line.split('=', 1)[1] for line in lines if line.startswith('mount=')]
        return {'reachable': True, 'cpu_count': int(data['cores']),
                'loadavg': [float(x) for x in data['load'].split()[:3]],
                'mem_available_bytes': int(float(data['mem_available'])),
                'mounts': {m: f'{m}:ok' in mount_lines for m in host.mounts},
                'westock_data': data.get('westock_data') == 'present'}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        return {'reachable': False, 'error': str(exc)[:200]}


def _ray(cluster):
    base = cluster.address.rstrip('/')
    try:
        data = _http(base + '/nodes?view=summary', 4)
        nodes = data.get('data', {}).get('summary', data.get('nodes', [])) if isinstance(data, dict) else data
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        alive = [n for n in nodes if str(n.get('state', n.get('raylet', {}).get('state', 'ALIVE'))).upper() == 'ALIVE']
        totals = {'CPU': 0.0, 'GPU': 0.0, 'memory': 0.0}
        available = totals.copy()
        for node in alive:
            for key in totals:
                totals[key] += float(node.get('raylet', {}).get('resourcesTotal', {}).get(key, 0))
        try:
            status = _http(base + '/api/cluster_status', 4)
            usage = status['data']['clusterStatus']['loadMetricsReport']['usage']
            for key in totals:
                if key in usage:
                    used, total = map(float, usage[key])
                    totals[key] = total
                    available[key] = max(0, total - used)
                else:
                    available[key] = totals[key]
        except (OSError, ValueError, TimeoutError, KeyError, TypeError):
            available = totals.copy()
        jobs = {}
        try:
            listing = _http(base + '/api/jobs/', 3)
            for job in listing if isinstance(listing, list) else listing.get('jobs', []):
                if str(job.get('submission_id', '')).startswith('alphasieve-'):
                    status = str(job.get('status', 'UNKNOWN'))
                    jobs[status] = jobs.get(status, 0) + 1
        except (OSError, ValueError, TimeoutError):
            pass
        return {'reachable': True, 'nodes_alive': len(alive), 'resources_total': totals,
                'resources_available': available,
                'resources_used': {k: totals[k] - available[k] for k in totals}, 'jobs': jobs}
    except (OSError, ValueError, TimeoutError, TypeError) as exc:
        return {'reachable': False, 'error': str(exc)[:200]}


def _llm(endpoint):
    start = time.monotonic()
    try:
        with urlopen(endpoint.url, timeout=3) as response:
            response.read(1024)
            return {'reachable': response.status == 200, 'latency_ms': round((time.monotonic()-start)*1000, 1),
                    'status': response.status}
    except (OSError, ValueError, TimeoutError) as exc:
        return {'reachable': False, 'latency_ms': round((time.monotonic()-start)*1000, 1),
                'error': str(exc)[:200]}


def snapshot(settings):
    targets = load_targets(settings)
    result = {'at': _now(), 'local': {}, 'ssh_hosts': {}, 'ray_clusters': {}, 'llm_endpoints': {}}
    tasks = [('local', None, _local)]
    tasks += [('ssh_hosts', h.name, lambda h=h: _ssh(h)) for h in targets.ssh_hosts]
    tasks += [('ray_clusters', c.name, lambda c=c: _ray(c)) for c in targets.ray_clusters]
    tasks += [('llm_endpoints', e.name, lambda e=e: _llm(e)) for e in targets.llm_endpoints]
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): (group, name) for group, name, fn in tasks}
        for future in as_completed(futures):
            group, name = futures[future]
            try:
                value = future.result()
            except Exception as exc:  # isolate a failed probe
                value = {'reachable': False, 'error': str(exc)[:200]}
            if name is None:
                result[group] = value
            else:
                result[group][name] = value
    return result


def _paths(settings):
    root = settings.hot_root / 'control'
    return root / 'resources.json', root / 'resources-history.jsonl'


def read_snapshot(settings):
    path, _ = _paths(settings)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_history(settings, hours=24):
    _, path = _paths(settings)
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    try:
        return [row for line in path.read_text().splitlines() if (row := json.loads(line))
                and datetime.fromisoformat(row['at']) >= cutoff]
    except (OSError, ValueError, KeyError):
        return []


def write_snapshot(settings):
    data = snapshot(settings)
    path, history = _paths(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.resources-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    compact = {'at': data['at'], 'local': {'loadavg': data['local'].get('loadavg'),
               'memory': data['local'].get('memory'), 'disks': data['local'].get('disks')},
               'ssh_hosts': {k: {'reachable': v.get('reachable'), 'loadavg': v.get('loadavg')}
                             for k, v in data['ssh_hosts'].items()},
               'ray_clusters': {k: {p: v.get(p) for p in ('reachable', 'nodes_alive', 'resources_used',
                                                          'resources_total')}
                                for k, v in data['ray_clusters'].items()},
               'llm_endpoints': {k: {'reachable': v.get('reachable'), 'latency_ms': v.get('latency_ms')}
                                 for k, v in data['llm_endpoints'].items()}}
    cutoff = datetime.now(UTC) - timedelta(days=7)
    lines = []
    if history.exists():
        for line in history.read_text().splitlines():
            try:
                if datetime.fromisoformat(json.loads(line)['at']) >= cutoff:
                    lines.append(line)
            except (ValueError, KeyError):
                continue
    lines.append(json.dumps(compact, ensure_ascii=False))
    fd, name = tempfile.mkstemp(dir=history.parent, prefix='.history-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join(lines) + '\n')
        os.replace(name, history)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return data
