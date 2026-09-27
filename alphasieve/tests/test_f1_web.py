from dataclasses import replace

from fastapi.testclient import TestClient
from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.contracts import FactorSpec
from alphasieve.evaluation.evaluate import evaluate_spec
from alphasieve.state import connect
from alphasieve.web.app import _load_credentials, create_app

REVERSAL = {"name": "rev_3d_excess", "expression": "ts_sum(excess_ret_1d, 3)", "direction": -1,
            "hypothesis": "short-term reversal", "cell": {"domain": "price", "form": "reversal", "scale": "short"}}


def test_web_requires_login_and_serves_read_models(panel_settings):
    start_campaign(panel_settings, campaign_spec("c-web"))
    conn = connect(panel_settings.state_db)
    result = evaluate_spec(replace(panel_settings, role="agent"), conn, FactorSpec(**REVERSAL), campaign_id="c-web")
    client = TestClient(create_app(panel_settings))
    auth = _load_credentials(panel_settings)
    assert (panel_settings.hot_root / "web.credentials").stat().st_mode & 0o077 == 0
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", auth=(auth[0], "wrong")).status_code == 401
    overview = client.get("/api/overview", auth=auth).json()
    assert overview["campaigns"][0]["campaign_id"] == "c-web" and overview["ledger"]["completed_trials"] == 1
    campaign = client.get("/api/campaigns/c-web", auth=auth).json()
    assert campaign["funnel"]["counts"]["submitted"] == 1 and len(campaign["intensity"]) == 1
    factors = client.get("/api/factors", auth=auth).json()
    assert factors["factors"][0]["factor_id"] == result["factor_id"]
    detail = client.get(f"/api/factors/{result['factor_id']}", auth=auth).json()
    assert detail["trials"][0]["evidence_tier"] == "dev" and detail["state_history"]
    ledger = client.get("/api/ledger", auth=auth).json()
    assert ledger["verify"]["ok"] and ledger["trials"]
    assert client.get("/api/campaigns/missing", auth=auth).status_code == 404
    assert client.get("/api/artifacts/..%2F..%2Fetc/report", auth=auth).status_code in (400, 404)
    assert client.get("/api/artifacts/not-an-artifact/report", auth=auth).status_code == 400
