"""System health checks (stub; lane HEALTH implements)."""


def collect(settings, conn):
    return {"checks": [], "overall": "unknown"}


def system_alerts(settings, conn, asof):
    return []


def record_system_alert(settings, conn, *, rule, subject, title, detail, severity="warning", evidence=(),
                        dedupe_key=None):
    return False
