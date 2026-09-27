"""Per-turn integrity checks. With process-only isolation (D-21) an agent could in principle bypass the CLI, so
the orchestrator compares protected state before and after each turn and scans the commands the agent ran."""

import re
import sqlite3
from pathlib import Path

from alphasieve.config import Settings
from alphasieve.util import file_sha256, sha256_hex

SUSPICIOUS = [
    (re.compile(r"ALPHASIEVE_(ROLE|CAMPAIGN|TURN|TURN_ALLOWANCE|HOT_ROOT|STORE_ROOT|CONFIG_DIR|EVAL_QUEUE)\s*="),
     "environment override"),
    (re.compile(r"panel/(holdout|fresh)"), "holdout or fresh panel path"),
    (re.compile(r"alphasieve\.db|sqlite3?\b"), "direct database access"),
    (re.compile(r"secrets\.env|auth\.json|AIHUB_API_KEY"), "credential access"),
    (re.compile(r"alphasieve\s+(holdout|review)\s+(approve|reject|decide)"), "human-only command"),
    (re.compile(r"\bconfigs?/(gate_policy|splits|costs|search_space)\.yaml"), "policy file access"),
    (re.compile(r"evalq/|/taijifs_[^ ]*/alphasieve"), "evaluation queue or platform data access"),
]


def _config_hash(settings: Settings) -> str:
    parts = [f"{p.name}:{file_sha256(p)}" for p in sorted(Path(settings.config_dir).glob("*.yaml"))]
    return sha256_hex("|".join(parts))


def snapshot(conn: sqlite3.Connection, settings: Settings) -> dict:
    one = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
    return {
        "holdout_requests": [tuple(r) for r in conn.execute(
            "SELECT request_id, status FROM holdout_requests ORDER BY request_id")],
        "decisions": one("SELECT COUNT(*) FROM decisions"),
        "review_packets": one("SELECT COUNT(*) FROM review_packets"),
        "non_dev_trials": one("SELECT COUNT(*) FROM trials WHERE evidence_tier != 'dev'"),
        "shortlists": one("SELECT COUNT(*) FROM shortlists"),
        "config": _config_hash(settings),
    }


def compare(before: dict, after: dict) -> list[str]:
    return [f"{k} changed during the turn" for k in before if before[k] != after[k]]


def scan_commands(commands: list[str], reads: list[str], workspace: Path) -> list[str]:
    findings = []
    for cmd in [*commands, *reads]:
        for pattern, label in SUSPICIOUS:
            if pattern.search(cmd or ""):
                findings.append(f"{label}: {cmd[:200]}")
    root = str(workspace.resolve())
    for path in reads:
        if path.startswith("/") and not str(Path(path).resolve()).startswith(root):
            findings.append(f"read outside workspace: {path[:200]}")
    return findings


def agent_events_with_other_roles(conn: sqlite3.Connection, since_seq: int, turn_id: str) -> list[str]:
    rows = conn.execute("SELECT seq, role, command, payload_json FROM events WHERE seq > ? AND role != 'agent'"
                        " AND json_extract(payload_json, '$.turn') = ?", (since_seq, turn_id)).fetchall()
    return [f"event {r['seq']} ran as {r['role']}: {r['command']}" for r in rows]
