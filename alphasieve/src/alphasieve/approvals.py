"""Human SSH signatures over immutable approval challenges."""

import json
import secrets
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError, validation_error
from alphasieve.util import canonical_json, sha256_hex

NAMESPACE = "alphasieve-approval"
KINDS = {"strategy_holdout", "factor_holdout", "review", "request"}


def _policy(settings: Settings) -> bool:
    path = settings.config_dir / "approvals.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return config.get("approvals", {}).get("require_signature", True) is not False


def allowed_signers(settings: Settings) -> Path:
    return settings.config_dir / "approvers" / "allowed_signers"


def fingerprints(settings: Settings) -> list[str]:
    result = []
    path = allowed_signers(settings)
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields or line.lstrip().startswith("#") or len(fields) < 3:
            continue
        key = " ".join(fields[1:3]) + "\n"
        proc = subprocess.run(["ssh-keygen", "-lf", "-"], input=key, text=True, capture_output=True, check=False)
        if proc.returncode == 0:
            result.append(proc.stdout.split()[1])
    return result


def _check_signers(settings: Settings) -> None:
    if not fingerprints(settings):
        raise AlphaSieveError(
            "PERMISSION_DENIED",
            "no human signing key is registered; add the public key to configs/approvers/allowed_signers",
        )


def evidence_hash(conn: sqlite3.Connection, settings: Settings, kind: str, target_id: str) -> str:
    if kind == "strategy_holdout":
        row = conn.execute(
            "SELECT request_id, mandate, task_id, trial_id, config_hash, status"
            " FROM strategy_holdout_requests WHERE request_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise validation_error(f"unknown strategy holdout request {target_id}")
        if row["status"] != "pending":
            raise AlphaSieveError("CONFLICT", "request is no longer pending")
        trial = conn.execute(
            "SELECT artifact_id FROM trials WHERE trial_id = ? AND record_kind = 'completed'"
            " AND layer = 'strategy' AND evidence_tier = 'dev'",
            (row["trial_id"],),
        ).fetchone()
        if trial is None:
            raise AlphaSieveError("CONFLICT", "locked dev trial is missing")
        artifact = settings.artifacts_dir / trial["artifact_id"] / "metrics.json"
        metrics = json.loads(artifact.read_text(encoding="utf-8"))
        bundle = metrics.get("bundle") or {}
        data = {
            **dict(row),
            "artifact_id": trial["artifact_id"],
            "artifact_sha256": sha256_hex(artifact.read_bytes()),
            "feature_version": bundle.get("feature_version"),
            "bundle_sha256": sha256_hex(canonical_json(bundle)),
        }
    elif kind == "factor_holdout":
        row = conn.execute(
            "SELECT request_id, shortlist_id, campaign_id, reads_requested, status"
            " FROM holdout_requests WHERE request_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise validation_error(f"unknown factor holdout request {target_id}")
        if row["status"] != "pending":
            raise AlphaSieveError("CONFLICT", "request is no longer pending")
        shortlist = conn.execute(
            "SELECT members_json, l3_json FROM shortlists WHERE shortlist_id = ?", (row["shortlist_id"],)
        ).fetchone()
        data = {**dict(row), "shortlist": dict(shortlist) if shortlist else None}
    elif kind == "review":
        row = conn.execute("SELECT * FROM review_packets WHERE packet_id = ?", (target_id,)).fetchone()
        if row is None:
            raise validation_error(f"unknown review packet {target_id}")
        if row["status"] != "open":
            raise AlphaSieveError("CONFLICT", "review is no longer open")
        packet = settings.artifacts_dir / row["artifact_id"] / "metrics.json"
        data = {**dict(row), "packet_sha256": sha256_hex(packet.read_bytes())}
    elif kind == "request":
        row = conn.execute(
            "SELECT request_id, campaign_id, kind, content, status FROM agent_requests WHERE request_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise validation_error(f"unknown agent request {target_id}")
        if row["status"] != "open":
            raise AlphaSieveError("CONFLICT", "request is no longer open")
        data = dict(row)
    else:
        raise validation_error(f"unknown approval kind {kind}")
    return sha256_hex(canonical_json(data))


def _decision(kind: str, decision: str) -> str:
    choices = {
        "strategy_holdout": {"approve", "reject"},
        "factor_holdout": {"approve", "reject"},
        "review": {"rejected", "needs_repair", "approved_for_shadow"},
        "request": {"approved", "rejected", "answered"},
    }
    if decision not in choices.get(kind, set()):
        raise validation_error(f"invalid decision {decision!r} for {kind}")
    return decision


def create_challenge(
    conn: sqlite3.Connection, settings: Settings, kind: str, target_id: str, decision: str, output: Path | None = None
) -> dict:
    _decision(kind, decision)
    if _policy(settings):
        _check_signers(settings)
    nonce = secrets.token_hex(24)
    payload = {
        "schema": "alphasieve.approval/v1",
        "kind": kind,
        "target_id": target_id,
        "decision": decision,
        "evidence_hash": evidence_hash(conn, settings, kind, target_id),
        "nonce": nonce,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }
    content = canonical_json(payload) + "\n"
    conn.execute(
        "INSERT INTO approval_challenges (nonce, kind, target_id, decision, content, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (nonce, kind, target_id, decision, content, payload["expires_at"]),
    )
    output = output or settings.hot_root / "approval_challenges" / f"{nonce}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return {
        "path": str(output),
        "content": content,
        "expires_at": payload["expires_at"],
        "sign_command": f"ssh-keygen -Y sign -n {NAMESPACE} -f <your-private-key> {output.name}",
        "fingerprints": fingerprints(settings),
    }


def preview_challenge(
    conn: sqlite3.Connection, settings: Settings, kind: str, target_id: str, decision: str
) -> dict | None:
    """Read a previously minted challenge without changing state (safe for GET handlers)."""
    row = conn.execute(
        "SELECT content, expires_at FROM approval_challenges WHERE kind = ? AND target_id = ?"
        " AND decision = ? AND used_at IS NULL ORDER BY rowid DESC LIMIT 1",
        (kind, target_id, decision),
    ).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
        return None
    try:
        if json.loads(row["content"])["evidence_hash"] != evidence_hash(conn, settings, kind, target_id):
            return None
    except (AlphaSieveError, KeyError, ValueError):
        return None
    return {
        "content": row["content"],
        "expires_at": row["expires_at"],
        "sign_command": f"ssh-keygen -Y sign -n {NAMESPACE} -f <your-private-key> challenge.txt",
    }


def verify_signature(settings: Settings, content: str, signature: str) -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "signature"
        path.write_text(signature, encoding="utf-8")
        proc = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed_signers(settings)),
                "-I",
                "human",
                "-n",
                NAMESPACE,
                "-s",
                str(path),
            ],
            input=content,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0


def consume_signature(
    conn: sqlite3.Connection, settings: Settings, kind: str, target_id: str, decision: str, signature_file: str | None
) -> dict | None:
    if not _policy(settings):
        return None
    _check_signers(settings)
    if not signature_file:
        raise validation_error("--signature is required for human approval")
    signature = Path(signature_file).read_text(encoding="utf-8")
    current_hash = evidence_hash(conn, settings, kind, target_id)
    rows = conn.execute(
        "SELECT * FROM approval_challenges WHERE kind = ? AND target_id = ? AND decision = ? AND used_at IS NULL",
        (kind, target_id, decision),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["content"])
        if payload["evidence_hash"] != current_hash or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            continue
        if verify_signature(settings, row["content"], signature):
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE approval_challenges SET used_at = ? WHERE nonce = ? AND used_at IS NULL", (now, row["nonce"])
            )
            previous = conn.execute("SELECT hash FROM signed_approvals ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = previous["hash"] if previous else "0" * 64
            head = conn.execute("SELECT hash FROM trials ORDER BY seq DESC LIMIT 1").fetchone()
            record = {
                "nonce": row["nonce"],
                "kind": kind,
                "target_id": target_id,
                "decision": decision,
                "content": row["content"],
                "signature": signature,
                "created_at": now,
                "prev_hash": prev_hash,
                "trial_head": head["hash"] if head else "0" * 64,
            }
            record["hash"] = sha256_hex(prev_hash + canonical_json(record))
            conn.execute(
                "INSERT INTO signed_approvals (nonce, kind, target_id, decision, content, signature,"
                " created_at, prev_hash, trial_head, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(record.values()),
            )
            return record
    raise AlphaSieveError("PERMISSION_DENIED", "signature invalid, expired, replayed, or evidence changed")


def verify_records(conn: sqlite3.Connection, settings: Settings) -> list[dict]:
    errors = []
    previous = "0" * 64
    for row in conn.execute("SELECT * FROM signed_approvals ORDER BY seq"):
        data = dict(row)
        expected = sha256_hex(
            previous
            + canonical_json(
                {
                    k: data[k]
                    for k in (
                        "nonce",
                        "kind",
                        "target_id",
                        "decision",
                        "content",
                        "signature",
                        "created_at",
                        "prev_hash",
                        "trial_head",
                    )
                }
            )
        )
        if data["prev_hash"] != previous or data["hash"] != expected:
            errors.append({"approval_seq": data["seq"], "error": "approval hash mismatch"})
        try:
            if not verify_signature(settings, data["content"], data["signature"]):
                errors.append({"approval_seq": data["seq"], "error": "approval signature invalid"})
            payload = json.loads(data["content"])
            if any(payload[k] != data[k] for k in ("nonce", "kind", "target_id", "decision")):
                errors.append({"approval_seq": data["seq"], "error": "approval content mismatch"})
        except (OSError, ValueError, KeyError, TypeError):
            errors.append({"approval_seq": data["seq"], "error": "malformed approval"})
        if (
            data["trial_head"] != "0" * 64
            and conn.execute("SELECT 1 FROM trials WHERE hash = ?", (data["trial_head"],)).fetchone() is None
        ):
            errors.append({"approval_seq": data["seq"], "error": "trial head missing"})
        if data["kind"] not in KINDS:
            errors.append({"approval_seq": data["seq"], "error": "unknown approval kind"})
        previous = data["hash"]
    return errors
