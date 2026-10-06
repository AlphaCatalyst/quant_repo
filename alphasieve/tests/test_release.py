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
