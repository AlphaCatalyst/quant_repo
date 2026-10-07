from fastapi.testclient import TestClient

from alphasieve.state import connect
from alphasieve.web.app import _load_credentials, create_app


def test_forward_requires_auth_even_when_other_pages_are_public(panel_settings):
    connect(panel_settings.state_db).close()
    client = TestClient(create_app(panel_settings, require_auth=False))
    assert client.get('/api/forward').status_code == 401
    assert client.get('/api/forward', auth=("nobody", "wrong")).status_code == 401
    assert client.get('/api/forward', auth=_load_credentials(panel_settings)).status_code == 200
    assert client.get('/api/overview').status_code == 200
