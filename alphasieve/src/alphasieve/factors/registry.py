import json
import sqlite3

from alphasieve.contracts import FactorSpec
from alphasieve.errors import not_found
from alphasieve.util import canonical_json, utcnow_iso


def register(conn: sqlite3.Connection, spec: FactorSpec, canonical: str, candidate_hash: str,
             created_by: str) -> tuple[str, int, bool]:
    """Return (factor_id, version, created). Identical candidates are deduplicated by hash.

    Runs in one write transaction so concurrent evaluations cannot allocate the same id.
    """
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT factor_id, version FROM factor_specs WHERE candidate_hash = ? ORDER BY factor_id, version LIMIT 1",
            (candidate_hash,),
        ).fetchone()
        if existing:
            result = (existing["factor_id"], existing["version"], False)
        else:
            same_name = conn.execute(
                "SELECT factor_id, MAX(version) AS v FROM factor_specs WHERE name = ? GROUP BY factor_id",
                (spec.name,),
            ).fetchone()
            if same_name:
                factor_id, version = same_name["factor_id"], same_name["v"] + 1
            else:
                count = conn.execute("SELECT COUNT(DISTINCT factor_id) AS n FROM factor_specs").fetchone()["n"]
                factor_id, version = f"F-{count + 1:06d}", 1
            conn.execute(
                "INSERT INTO factor_specs (factor_id, version, candidate_hash, name, spec_json, canonical_expression,"
                " state, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                (factor_id, version, candidate_hash, spec.name, canonical_json(spec.model_dump()), canonical,
                 created_by, utcnow_iso()),
            )
            result = (factor_id, version, True)
        if own_tx:
            conn.execute("COMMIT")
        return result
    except Exception:
        if own_tx:
            conn.execute("ROLLBACK")
        raise


def parse_ref(ref: str) -> tuple[str, int | None]:
    if "@" in ref:
        factor_id, version = ref.split("@", 1)
        return factor_id, int(version)
    return ref, None


def get_factor(conn: sqlite3.Connection, ref: str) -> dict:
    factor_id, version = parse_ref(ref)
    if version is None:
        row = conn.execute("SELECT * FROM factor_specs WHERE factor_id = ? ORDER BY version DESC LIMIT 1",
                           (factor_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM factor_specs WHERE factor_id = ? AND version = ?",
                           (factor_id, version)).fetchone()
    if row is None:
        raise not_found(f"factor {ref} not found")
    out = dict(row)
    out["spec"] = json.loads(out.pop("spec_json"))
    return out


def list_factors(conn: sqlite3.Connection, state: str | None = None, limit: int = 100) -> list[dict]:
    query = "SELECT factor_id, version, name, state, candidate_hash, canonical_expression, created_by, created_at" \
            " FROM factor_specs"
    params: list = []
    if state:
        query += " WHERE state = ?"
        params.append(state)
    query += " ORDER BY created_at DESC, factor_id, version LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params)]


def neighborhood_count(conn: sqlite3.Connection, anchor: str | None) -> int:
    if not anchor:
        return 0
    rows = conn.execute("SELECT spec_json FROM factor_specs").fetchall()
    return sum(1 for r in rows if json.loads(r["spec_json"]).get("neighborhood_of") == anchor)
