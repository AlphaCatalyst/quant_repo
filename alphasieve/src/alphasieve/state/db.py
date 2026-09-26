import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE factor_specs (
        factor_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        candidate_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        spec_json TEXT NOT NULL,
        canonical_expression TEXT NOT NULL,
        state TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (factor_id, version)
    );
    CREATE INDEX factor_specs_hash ON factor_specs(candidate_hash);
    CREATE INDEX factor_specs_name ON factor_specs(name);

    CREATE TABLE trials (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        trial_id TEXT NOT NULL,
        record_kind TEXT NOT NULL CHECK (record_kind IN ('started', 'completed', 'failed', 'void')),
        campaign_id TEXT,
        factor_id TEXT,
        version INTEGER,
        candidate_hash TEXT,
        evidence_tier TEXT NOT NULL,
        data_window TEXT,
        gate_policy_version INTEGER,
        search_space_version TEXT,
        metrics_json TEXT NOT NULL,
        gate_results_json TEXT NOT NULL,
        outcome TEXT,
        created_by TEXT NOT NULL,
        artifact_id TEXT,
        created_at TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX trials_trial_id ON trials(trial_id);
    CREATE INDEX trials_campaign ON trials(campaign_id);
    CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
    BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
    CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
    BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;

    CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        role TEXT NOT NULL,
        user TEXT NOT NULL,
        command TEXT,
        event_type TEXT NOT NULL,
        object_type TEXT,
        object_id TEXT,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL
    );

    CREATE TABLE data_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        dataset TEXT NOT NULL,
        params_json TEXT NOT NULL,
        rows INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        path TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE library (
        candidate_hash TEXT PRIMARY KEY,
        factor_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        source TEXT NOT NULL,
        added_by TEXT NOT NULL,
        added_at TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE library ADD COLUMN name TEXT;
    ALTER TABLE library ADD COLUMN metrics_json TEXT;
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0
    for index, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split_sql(sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (index,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _split_sql(sql: str) -> list[str]:
    statements, buffer, in_trigger = [], [], False
    for line in sql.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        buffer.append(line)
        upper = stripped.upper()
        if upper.startswith("CREATE TRIGGER"):
            in_trigger = True
        if in_trigger:
            if upper.endswith("END;"):
                statements.append("\n".join(buffer))
                buffer, in_trigger = [], False
        elif stripped.endswith(";"):
            statements.append("\n".join(buffer))
            buffer = []
    if buffer:
        statements.append("\n".join(buffer))
    return statements
