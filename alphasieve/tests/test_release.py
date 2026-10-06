"""Release script's read-only dry run selects the requested commit."""

import subprocess
from pathlib import Path


def test_release_dry_run(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'file').write_text('x')
    subprocess.run(['git', '-C', str(repo), 'add', 'file'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'test'], check=True)
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    script = Path(__file__).resolve().parents[1] / 'deploy' / 'release.sh'
    assert subprocess.run(['bash', '-n', str(script)]).returncode == 0
    (repo / 'deploy').mkdir()
    (repo / 'deploy' / 'release.sh').write_text(script.read_text())
    out = subprocess.check_output(['bash', str(repo / 'deploy' / 'release.sh'), sha, '--dry-run'], text=True)
    assert sha in out and 'test exact commit' in out
    out = subprocess.check_output(['bash', str(repo / 'deploy' / 'release.sh'), '--skip-tests', '--dry-run'], text=True)
    assert 'WARNING' in out


def test_release_dry_run_in_subdirectory_project(tmp_path):
    repo = tmp_path / 'repo'
    project = repo / 'alphasieve'
    (project / 'deploy').mkdir(parents=True)
    script = Path(__file__).resolve().parents[1] / 'deploy' / 'release.sh'
    (project / 'deploy' / 'release.sh').write_text(script.read_text())
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'test'], check=True)
    out = subprocess.check_output(['bash', str(project / 'deploy' / 'release.sh'), '--dry-run'], text=True,
                                  env={'PATH': '/usr/bin:/bin', 'ALPHASIEVE_DEPLOY_ROOT': str(tmp_path / 'd')})
    assert f"{tmp_path / 'd'}/releases/" in out and '/alphasieve; uv sync' in out
