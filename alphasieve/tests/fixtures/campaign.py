from dataclasses import replace

from alphasieve.campaigns import service
from alphasieve.contracts import Campaign
from alphasieve.state import connect

DEFAULT_DOMAINS = ["price", "volume", "turnover_liquidity", "size_value", "profitability", "growth"]


def campaign_spec(campaign_id: str = "test-campaign", domains: list[str] | None = None, **overrides) -> Campaign:
    data = {
        "campaign_id": campaign_id,
        "title": "test campaign",
        "question": "does anything work on synthetic data?",
        "domains": domains or DEFAULT_DOMAINS,
        "cells": [{"domain": "price", "form": "reversal", "scale": "short"}],
        "agents": [{"harness": "fake", "model": "fake"}],
    }
    data.update(overrides)
    return Campaign(**data)


def start_campaign(settings, spec: Campaign, monkeypatch=None) -> str:
    human = replace(settings, role="human")
    conn = connect(settings.state_db)
    service.create_campaign(conn, human, spec)
    service.set_status(conn, human, spec.campaign_id, "running")
    conn.close()
    if monkeypatch is not None:
        monkeypatch.setenv("ALPHASIEVE_CAMPAIGN", spec.campaign_id)
    return spec.campaign_id
