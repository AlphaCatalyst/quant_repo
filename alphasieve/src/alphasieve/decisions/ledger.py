"""PD-4: decision-layer trial records, counted per task against its pre-registered budget."""

import json
import sqlite3
import uuid

from alphasieve.errors import validation_error
from alphasieve.util import utcnow_iso


def trial_count(conn: sqlite3.Connection, task: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM decision_trials WHERE task=?", (task,)).fetchone()[0]


def check_budget(conn: sqlite3.Connection, task: str, budget: int, adding: int) -> None:
    used = trial_count(conn, task)
    if used + adding > budget:
        raise validation_error("decision trial budget exhausted", task=task, used=used, adding=adding,
                               budget=budget)


def record(conn: sqlite3.Connection, task: str, run_id: str, rule_id: str, pool: str, config_hash: str,
           metrics: dict, actor: str, tier: str = "dev") -> str:
    trial_id = uuid.uuid4().hex
    conn.execute("INSERT INTO decision_trials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (trial_id, task, run_id, rule_id, pool, tier, config_hash,
                  json.dumps(metrics, ensure_ascii=False, allow_nan=False), actor, utcnow_iso()))
    return trial_id


def list_trials(conn: sqlite3.Connection, task: str | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM decision_trials" + (" WHERE task=?" if task else "") + " ORDER BY created_at",
                        (task,) if task else ()).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["metrics"] = json.loads(item.pop("metrics_json"))
        result.append(item)
    return result
