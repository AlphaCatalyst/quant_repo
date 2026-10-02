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
    """
    CREATE TABLE campaigns (
        campaign_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        spec_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        concluded_at TEXT,
        memory_frozen_at TEXT,
        stats_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE turns (
        turn_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        harness TEXT NOT NULL,
        model TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        transcript_path TEXT,
        prompt_version TEXT,
        trials_before INTEGER,
        trials_after INTEGER,
        robust_passed_new INTEGER,
        usage_json TEXT NOT NULL DEFAULT '{}',
        summary TEXT,
        error TEXT
    );
    CREATE INDEX turns_campaign ON turns(campaign_id, turn_index);
    CREATE TABLE directives (
        directive_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        consumed_turn_id TEXT
    );
    CREATE TABLE agent_requests (
        request_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL,
        response TEXT,
        created_at TEXT NOT NULL,
        responded_by TEXT,
        responded_at TEXT
    );
    CREATE TABLE memory_items (
        item_id TEXT PRIMARY KEY,
        campaign_id TEXT,
        scope TEXT NOT NULL,
        kind TEXT NOT NULL,
        content_json TEXT NOT NULL,
        source_trials TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    );
    CREATE TABLE shortlists (
        shortlist_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        members_json TEXT NOT NULL,
        l3_json TEXT NOT NULL,
        status TEXT NOT NULL,
        locked_at TEXT NOT NULL,
        locked_by TEXT NOT NULL
    );
    CREATE TABLE holdout_requests (
        request_id TEXT PRIMARY KEY,
        shortlist_id TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        reads_requested INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_by TEXT,
        decided_at TEXT,
        reason TEXT,
        result_json TEXT
    );
    CREATE TABLE review_packets (
        packet_id TEXT PRIMARY KEY,
        factor_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        campaign_id TEXT NOT NULL,
        shortlist_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE decisions (
        decision_id TEXT PRIMARY KEY,
        object_type TEXT NOT NULL,
        object_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL,
        decided_by TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        evidence_hash TEXT
    );
    """,
    """
    ALTER TABLE trials ADD COLUMN turn_id TEXT;
    CREATE INDEX trials_turn ON trials(turn_id);
    """,
    """
    ALTER TABLE trials ADD COLUMN layer TEXT NOT NULL DEFAULT 'factor';
    ALTER TABLE trials ADD COLUMN scope TEXT;
    CREATE INDEX trials_layer_scope ON trials(layer, scope);
    CREATE TABLE strategy_holdout_requests (
        request_id TEXT PRIMARY KEY,
        mandate TEXT NOT NULL,
        task_id TEXT NOT NULL,
        trial_id TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_by TEXT,
        decided_at TEXT,
        reason TEXT,
        result_json TEXT
    );
    """,
    """
    CREATE TABLE approval_challenges (
        nonce TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL,
        decision TEXT NOT NULL, content TEXT NOT NULL, evidence_json TEXT NOT NULL,
        expires_at TEXT NOT NULL, used_at TEXT
    );
    CREATE TABLE signed_approvals (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, nonce TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, target_id TEXT NOT NULL, decision TEXT NOT NULL,
        content TEXT NOT NULL, evidence_json TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL,
        prev_hash TEXT NOT NULL, trial_head TEXT NOT NULL, hash TEXT NOT NULL
    );
    CREATE TRIGGER signed_approvals_no_update BEFORE UPDATE ON signed_approvals
    BEGIN SELECT RAISE(ABORT, 'signed approvals are append-only'); END;
    CREATE TRIGGER signed_approvals_no_delete BEFORE DELETE ON signed_approvals
    BEGIN SELECT RAISE(ABORT, 'signed approvals are append-only'); END;
    CREATE TABLE signed_approval_outcomes (
        nonce TEXT PRIMARY KEY, decision_id TEXT, applied_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE fresh_cohorts (
        cohort_id TEXT PRIMARY KEY, object_kind TEXT NOT NULL, mode TEXT NOT NULL,
        trial_id TEXT, source_hash TEXT NOT NULL, config_json TEXT NOT NULL,
        config_hash TEXT NOT NULL, policy_json TEXT NOT NULL, policy_hash TEXT NOT NULL,
        start_date TEXT NOT NULL, approval_id TEXT NOT NULL, approval_by TEXT NOT NULL,
        approval_at TEXT NOT NULL, parent_id TEXT
    );
    CREATE TABLE fresh_members (
        cohort_id TEXT NOT NULL, object_version_hash TEXT NOT NULL,
        member_json TEXT NOT NULL, PRIMARY KEY(cohort_id, object_version_hash)
    );
    CREATE TABLE fresh_days (
        universe TEXT NOT NULL, date TEXT NOT NULL, cutoff TEXT NOT NULL,
        input_hash TEXT NOT NULL, partition_hash TEXT NOT NULL, benchmark_hash TEXT NOT NULL,
        prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL, manifest_json TEXT NOT NULL,
        PRIMARY KEY(universe, date)
    );
    CREATE TABLE forward_runs (
        run_id TEXT PRIMARY KEY, cohort_id TEXT NOT NULL, date TEXT NOT NULL,
        kind TEXT NOT NULL, status TEXT NOT NULL, input_hash TEXT, model_id TEXT,
        details_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE model_snapshots (
        model_id TEXT PRIMARY KEY, cohort_id TEXT NOT NULL, date TEXT NOT NULL,
        digest TEXT NOT NULL, manifest_json TEXT NOT NULL
    );
    CREATE TABLE fresh_observations (
        cohort_id TEXT NOT NULL, member TEXT NOT NULL, signal_date TEXT NOT NULL,
        horizon INTEGER NOT NULL, maturity_date TEXT NOT NULL, metric REAL,
        reason TEXT, endpoints_json TEXT NOT NULL,
        PRIMARY KEY(cohort_id, member, signal_date, horizon)
    );
    CREATE TABLE fresh_verdicts (
        cohort_id TEXT PRIMARY KEY, verdict TEXT NOT NULL, metrics_json TEXT NOT NULL,
        policy_hash TEXT NOT NULL, decided_at TEXT NOT NULL
    );
    CREATE TABLE paper_books (
        book_id TEXT PRIMARY KEY, cohort_id TEXT NOT NULL, book_mode TEXT NOT NULL,
        approval_id TEXT, capital REAL NOT NULL, benchmark TEXT NOT NULL,
        start_date TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE paper_targets (
        book_id TEXT NOT NULL, date TEXT NOT NULL, target_json TEXT NOT NULL,
        digest TEXT NOT NULL, sealed_at TEXT NOT NULL, PRIMARY KEY(book_id,date)
    );
    CREATE TABLE paper_days (
        book_id TEXT NOT NULL, date TEXT NOT NULL, status TEXT NOT NULL,
        nav REAL, benchmark_nav REAL, ret REAL, benchmark_ret REAL,
        checkpoint_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
        prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL, manifest_json TEXT NOT NULL,
        PRIMARY KEY(book_id,date)
    );
    CREATE TABLE paper_fills (
        book_id TEXT NOT NULL, date TEXT NOT NULL, code TEXT NOT NULL,
        fill_json TEXT NOT NULL, PRIMARY KEY(book_id,date,code)
    );
    CREATE TABLE forward_ledger (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, record_kind TEXT NOT NULL,
        cohort_id TEXT, run_id TEXT, book_id TEXT, date TEXT, actor TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL
    );
    CREATE TRIGGER fresh_members_no_update BEFORE UPDATE ON fresh_members
    BEGIN SELECT RAISE(ABORT,'fresh_members is append-only'); END;
    CREATE TRIGGER fresh_members_no_delete BEFORE DELETE ON fresh_members
    BEGIN SELECT RAISE(ABORT,'fresh_members is append-only'); END;
    CREATE TRIGGER forward_runs_no_update BEFORE UPDATE ON forward_runs
    BEGIN SELECT RAISE(ABORT,'forward_runs is append-only'); END;
    CREATE TRIGGER forward_runs_no_delete BEFORE DELETE ON forward_runs
    BEGIN SELECT RAISE(ABORT,'forward_runs is append-only'); END;
    CREATE TRIGGER model_snapshots_no_update BEFORE UPDATE ON model_snapshots
    BEGIN SELECT RAISE(ABORT,'model_snapshots is append-only'); END;
    CREATE TRIGGER model_snapshots_no_delete BEFORE DELETE ON model_snapshots
    BEGIN SELECT RAISE(ABORT,'model_snapshots is append-only'); END;
    CREATE TRIGGER fresh_observations_no_update BEFORE UPDATE ON fresh_observations
    BEGIN SELECT RAISE(ABORT,'fresh_observations is append-only'); END;
    CREATE TRIGGER fresh_observations_no_delete BEFORE DELETE ON fresh_observations
    BEGIN SELECT RAISE(ABORT,'fresh_observations is append-only'); END;
    CREATE TRIGGER fresh_verdicts_no_update BEFORE UPDATE ON fresh_verdicts
    BEGIN SELECT RAISE(ABORT,'fresh_verdicts is append-only'); END;
    CREATE TRIGGER fresh_verdicts_no_delete BEFORE DELETE ON fresh_verdicts
    BEGIN SELECT RAISE(ABORT,'fresh_verdicts is append-only'); END;
    CREATE TRIGGER paper_books_no_update BEFORE UPDATE ON paper_books
    BEGIN SELECT RAISE(ABORT,'paper_books is append-only'); END;
    CREATE TRIGGER paper_books_no_delete BEFORE DELETE ON paper_books
    BEGIN SELECT RAISE(ABORT,'paper_books is append-only'); END;
    CREATE TRIGGER paper_targets_no_update BEFORE UPDATE ON paper_targets
    BEGIN SELECT RAISE(ABORT,'paper_targets is append-only'); END;
    CREATE TRIGGER paper_targets_no_delete BEFORE DELETE ON paper_targets
    BEGIN SELECT RAISE(ABORT,'paper_targets is append-only'); END;
    CREATE TRIGGER paper_fills_no_update BEFORE UPDATE ON paper_fills
    BEGIN SELECT RAISE(ABORT,'paper_fills is append-only'); END;
    CREATE TRIGGER paper_fills_no_delete BEFORE DELETE ON paper_fills
    BEGIN SELECT RAISE(ABORT,'paper_fills is append-only'); END;
    CREATE TRIGGER fresh_cohorts_no_update BEFORE UPDATE ON fresh_cohorts
    BEGIN SELECT RAISE(ABORT,'fresh_cohorts is append-only'); END;
    CREATE TRIGGER fresh_cohorts_no_delete BEFORE DELETE ON fresh_cohorts
    BEGIN SELECT RAISE(ABORT,'fresh_cohorts is append-only'); END;
    CREATE TRIGGER fresh_days_no_update BEFORE UPDATE ON fresh_days
    BEGIN SELECT RAISE(ABORT,'fresh_days is append-only'); END;
    CREATE TRIGGER fresh_days_no_delete BEFORE DELETE ON fresh_days
    BEGIN SELECT RAISE(ABORT,'fresh_days is append-only'); END;
    CREATE TRIGGER paper_days_no_update BEFORE UPDATE ON paper_days
    BEGIN SELECT RAISE(ABORT,'paper_days is append-only'); END;
    CREATE TRIGGER paper_days_no_delete BEFORE DELETE ON paper_days
    BEGIN SELECT RAISE(ABORT,'paper_days is append-only'); END;
    CREATE TRIGGER forward_ledger_no_update BEFORE UPDATE ON forward_ledger
    BEGIN SELECT RAISE(ABORT,'forward_ledger is append-only'); END;
    CREATE TRIGGER forward_ledger_no_delete BEFORE DELETE ON forward_ledger
    BEGIN SELECT RAISE(ABORT,'forward_ledger is append-only'); END;
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
    if (conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"] or 0) >= len(MIGRATIONS):
        return len(MIGRATIONS)
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"] or 0
        for index, sql in enumerate(MIGRATIONS[current:], start=current + 1):
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
