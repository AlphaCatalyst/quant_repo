import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alphasieve.approvals import consume_signature, create_challenge, verify_records
from alphasieve.config import get_settings
from alphasieve.errors import AlphaSieveError
from alphasieve.ledger.ledger import verify_ledger
from alphasieve.state.db import connect


def _key(tmp_path: Path, name: str) -> Path:
    key = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


def _sign(key: Path, content: Path) -> Path:
    Path(str(content) + ".sig").unlink(missing_ok=True)
    subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-n", "alphasieve-approval", "-f", str(key), str(content)],
        check=True,
        capture_output=True,
    )
    return Path(str(content) + ".sig")


def test_approval_signature_and_rejections(tmp_path):
    config = tmp_path / "config"
    (config / "approvers").mkdir(parents=True)
    (config / "approvals.yaml").write_text("approvals:\n  require_signature: true\n", encoding="utf-8")
    key = _key(tmp_path, "human")
    wrong = _key(tmp_path, "wrong")
    (config / "approvers" / "allowed_signers").write_text(
        "human " + key.with_suffix(".pub").read_text(encoding="utf-8"), encoding="utf-8"
    )
    settings = replace(
        get_settings(), hot_root=tmp_path / "hot", store_root=tmp_path / "store", store_mount=None, config_dir=config
    )
    conn = connect(settings.state_db)
    for request_id in ("R-1", "R-2"):
        conn.execute(
            "INSERT INTO agent_requests (request_id, campaign_id, kind, content, status, created_at)"
            " VALUES (?, 'C', 'question', 'What next?', 'open', ?)",
            (request_id, datetime.now(UTC).isoformat()),
        )
    with pytest.raises(AlphaSieveError, match="no human signing key"):
        empty = replace(settings, config_dir=tmp_path / "empty")
        create_challenge(conn, empty, "request", "R-1", "answered")
    challenge = create_challenge(conn, settings, "request", "R-1", "answered", tmp_path / "challenge.txt")
    signed = _sign(key, Path(challenge["path"]))
    with pytest.raises(AlphaSieveError, match="invalid"):
        consume_signature(conn, settings, "request", "R-2", "answered", str(signed))
    wrong_signature = _sign(wrong, Path(challenge["path"]))
    with pytest.raises(AlphaSieveError, match="invalid"):
        consume_signature(conn, settings, "request", "R-1", "answered", str(wrong_signature))
    signed = _sign(key, Path(challenge["path"]))
    conn.execute("UPDATE agent_requests SET content = 'changed' WHERE request_id = 'R-1'")
    with pytest.raises(AlphaSieveError, match="invalid"):
        consume_signature(conn, settings, "request", "R-1", "answered", str(signed))
    conn.execute("UPDATE agent_requests SET content = 'What next?' WHERE request_id = 'R-1'")
    conn.execute(
        "UPDATE approval_challenges SET expires_at = ?", ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),)
    )
    with pytest.raises(AlphaSieveError, match="invalid"):
        consume_signature(conn, settings, "request", "R-1", "answered", str(signed))
    challenge = create_challenge(conn, settings, "request", "R-1", "answered", tmp_path / "fresh.txt")
    signed = _sign(key, Path(challenge["path"]))
    consume_signature(conn, settings, "request", "R-1", "answered", str(signed))
    with pytest.raises(AlphaSieveError, match="invalid"):
        consume_signature(conn, settings, "request", "R-1", "answered", str(signed))
    assert verify_records(conn, settings) == []
    conn.execute("DROP TRIGGER signed_approvals_no_update")
    conn.execute("UPDATE signed_approvals SET evidence_json = '{}' ")
    assert any("evidence" in error["error"] for error in verify_records(conn, settings))
    conn.execute("UPDATE signed_approvals SET signature = 'tampered'")
    assert any("signature" in error["error"] for error in verify_records(conn, settings))
    assert not verify_ledger(conn, settings)["ok"]
