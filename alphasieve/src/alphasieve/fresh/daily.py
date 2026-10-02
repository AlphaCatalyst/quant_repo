"""Forward day coordinator; only sealed snapshots enter the observation book."""
import sqlite3

from alphasieve.config import Settings
from alphasieve.fresh.service import append_ledger


def run_day(settings: Settings, conn: sqlite3.Connection, cohort: dict, asof: str) -> dict:
    """Refuse a day until a locked model-to-target pipeline has sealed its signal."""
    cid = cohort['cohort_id']
    reason = 'locked model-to-target daily pipeline is not configured'
    prior = conn.execute("SELECT 1 FROM forward_ledger WHERE cohort_id=? AND date=? AND record_kind='blocked'",
                         (cid, asof)).fetchone()
    if not prior:
        append_ledger(conn, 'blocked', 'system', {'reason': reason}, cid, asof, f'B-{cid}')
    return {'cohort_id': cid, 'date': asof, 'status': 'blocked', 'reason': reason}
