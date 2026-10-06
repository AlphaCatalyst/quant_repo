"""System health checks (stub; lane HEALTH implements)."""


def collect(settings, conn):
    return {"checks": [], "overall": "unknown"}


def system_alerts(settings, conn, asof):
    return []
