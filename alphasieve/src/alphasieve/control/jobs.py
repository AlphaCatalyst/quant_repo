"""Job layer (stub; lane JOBS implements)."""


def list_jobs(conn, *, open_only=False, kind=None, limit=200):
    return []


def job_summary(conn):
    return {"by_status": {}, "by_kind": {}, "by_placement": {}, "recent_failures": []}
