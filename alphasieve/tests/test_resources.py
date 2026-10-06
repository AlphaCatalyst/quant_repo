"""Bounded resource probes with synthetic targets."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from alphasieve.control import resources
from alphasieve.control.targets import LlmEndpoint, RayCluster, SshHost


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/nodes'):
            body = {'data': {'summary': [{'raylet': {'state': 'ALIVE', 'resourcesTotal':
                    {'CPU': 8, 'GPU': 1, 'memory': 100}}}]}}
        elif self.path == '/api/cluster_status':
            body = {'data': {'clusterStatus': {'loadMetricsReport': {'usage':
                    {'CPU': [3, 8], 'GPU': [1, 1], 'memory': [25, 100]}}}}}
        elif self.path.startswith('/api/jobs'):
            body = [{'submission_id': 'alphasieve-1', 'status': 'RUNNING'},
                    {'submission_id': 'other-2', 'status': 'RUNNING'}]
        else:
            body = {'ok': True}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_ray_and_llm_probe():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        ray = resources._ray(RayCluster('test', url, True, 1))
        assert ray['nodes_alive'] == 1
        assert ray['resources_used'] == {'CPU': 3, 'GPU': 1, 'memory': 25}
        assert ray['jobs'] == {'RUNNING': 1}
        assert resources._llm(LlmEndpoint('llm', url + '/health'))['reachable']
    finally:
        server.shutdown()


def test_ssh_banner_and_dead_target(tmp_path, monkeypatch):
    ssh = tmp_path / 'ssh'
    ssh.write_text('#!/bin/sh\necho authz success\necho cores=16\necho "load=1 2 3 1/1 42"\n'
                   'echo mem_available=1024\necho mount=/mnt/test:ok\necho westock_data=present\n')
    ssh.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + __import__('os').environ['PATH'])
    assert resources._ssh(SshHost('test', 'test', ('/mnt/test',)))['mounts']['/mnt/test']
    start = time.monotonic()
    assert not resources._llm(LlmEndpoint('dead', 'http://127.0.0.1:1/health'))['reachable']
    assert time.monotonic() - start < 3


def test_local_ps_grouping(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(resources, '_memory', lambda: {'MemTotal': 100, 'MemAvailable': 50})
    monkeypatch.setattr(resources.os, 'getloadavg', lambda: (1, 2, 3))
    monkeypatch.setattr(resources.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(
        stdout='1 12.5 100 python alphasieve\n2 5.0 200 scicomp-foundry worker\n'
        if args[0] == 'ps' else '', returncode=0))
    monkeypatch.setattr(resources.Path, 'read_text', lambda self: '')
    local = resources._local()
    assert local['groups']['alphasieve']['cpu_percent'] == 12.5
    assert local['groups']['scicomp-foundry']['rss_bytes'] == 200 * 1024


def test_slow_endpoint_bounded():
    class SlowHandler(Handler):
        def do_GET(self):
            time.sleep(5)
            try:
                super().do_GET()
            except BrokenPipeError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        start = time.monotonic()
        result = resources._llm(LlmEndpoint('slow', f'http://127.0.0.1:{server.server_port}/health'))
        assert not result['reachable']
        assert time.monotonic() - start < 4
    finally:
        server.shutdown()


def test_snapshot_history_retention(settings, monkeypatch):
    from datetime import UTC, datetime, timedelta

    root = settings.hot_root / 'control'
    root.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    (root / 'resources-history.jsonl').write_text(json.dumps({'at': old}) + '\n')
    monkeypatch.setattr(resources, 'snapshot', lambda _settings: {
        'at': datetime.now(UTC).isoformat(), 'local': {'loadavg': [1, 2, 3], 'memory': {}, 'disks': {}},
        'ssh_hosts': {}, 'ray_clusters': {}, 'llm_endpoints': {}})
    resources.write_snapshot(settings)
    assert resources.read_snapshot(settings)['local']['loadavg'] == [1, 2, 3]
    assert len(resources.read_history(settings)) == 1


def test_scicomp_container_cgroup():
    assert resources._group('python worker', '0::/docker/abc123def4567890', {'abc123def456'}) == 'scicomp-foundry'
