import json

from alphasieve import __version__
from alphasieve.cli.main import main


def test_version_json(capsys):
    assert main(["version", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "version": __version__}
