from fastapi.testclient import TestClient

from alphasieve.state import connect
from alphasieve.web.app import _load_credentials, create_app


def test_forward_follows_board_auth(panel_settings):
    connect(panel_settings.state_db).close()
    open_client = TestClient(create_app(panel_settings, require_auth=False))
    assert open_client.get('/api/forward').status_code == 200
    assert open_client.get('/api/overview').status_code == 200
    client = TestClient(create_app(panel_settings, require_auth=True))
    assert client.get('/api/forward').status_code == 401
    assert client.get('/api/forward', auth=("nobody", "wrong")).status_code == 401
    assert client.get('/api/forward', auth=_load_credentials(panel_settings)).status_code == 200
