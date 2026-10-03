"""Import broker exports into the local append-only holdings table."""

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from alphasieve.config import PACKAGE_CONFIG_DIR
from alphasieve.errors import not_found, validation_error
from alphasieve.util import canonical_json, utcnow_iso

DEFAULT_MAPPING = PACKAGE_CONFIG_DIR / "book" / "mapping_generic.yaml"


def normalize_code(value: object) -> str:
    raw = str(value).strip().lower()
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    match = re.fullmatch(r"(sh|sz|bj)[.](\d{6})", raw)
    if match:
        return raw
    match = re.fullmatch(r"(\d{6})[.](sh|sz|bj)", raw)
    if match:
        return f"{match[2]}.{match[1]}"
    if not re.fullmatch(r"\d{6}", raw):
        raise validation_error("invalid security code", code=raw)
    if raw.startswith(("6", "9")):
        exchange = "sh"
    elif raw.startswith(("4", "8")):
        exchange = "bj"
    else:
        exchange = "sz"
    return f"{exchange}.{raw}"


def _number(value: object, field: str, required: bool = False) -> float | None:
    if pd.isna(value) or str(value).strip() == "":
        if required:
            raise validation_error(f"missing {field}")
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (ValueError, TypeError) as exc:
        raise validation_error(f"invalid {field}", value=str(value)) from exc
    if not (-float("inf") < result < float("inf")) or result < 0:
        raise validation_error(f"invalid {field}", value=str(value))
    return result


def _read_export(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise validation_error("holdings export not found", path=str(path))
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)
    if path.suffix.lower() != ".csv":
        raise validation_error("holdings export must be CSV or XLSX")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise validation_error("unsupported CSV encoding")


def parse_export(path: Path, mapping: Path = DEFAULT_MAPPING, cash: float | None = None) -> list[dict]:
    config = yaml.safe_load(mapping.read_text(encoding="utf-8"))
    aliases = config.get("columns", {})
    frame = _read_export(path)
    headers = {str(column).strip().lower(): column for column in frame.columns}
    columns = {key: next((headers[str(alias).strip().lower()] for alias in names
                          if str(alias).strip().lower() in headers), None) for key, names in aliases.items()}
    if columns.get("code") is None or columns.get("quantity") is None:
        raise validation_error("export needs code and quantity columns")
    cash_labels = {str(label).strip().lower() for label in config.get("cash_labels", [])}
    positions: dict[str, dict] = {}
    cash_total = _number(cash, "cash") or 0.0
    cash_seen = cash is not None
    for _, row in frame.iterrows():
        def get(key, current_row=row):
            return current_row[columns[key]] if columns.get(key) is not None else None
        raw_code = "" if pd.isna(get("code")) else str(get("code")).strip()
        kind = "" if pd.isna(get("asset_type")) else str(get("asset_type")).strip().lower()
        if raw_code.lower() in cash_labels or kind in cash_labels:
            amount = _number(get("market_value"), "cash")
            if amount is None:
                amount = _number(get("cash"), "cash")
            if amount is None:
                amount = _number(get("quantity"), "cash", required=True)
            cash_total += amount
            cash_seen = True
            continue
        if not raw_code and all(pd.isna(value) for value in row):
            continue
        code = normalize_code(raw_code)
        if code in positions:
            raise validation_error("duplicate security code", code=code)
        quantity = _number(get("quantity"), "quantity", required=True)
        price = _number(get("price"), "price")
        market_value = _number(get("market_value"), "market_value")
        if market_value is None and price is not None:
            market_value = quantity * price
        if market_value is None:
            raise validation_error("position needs market_value or price", code=code)
        positions[code] = {"code": code, "name": str(get("name") or "").strip(), "quantity": quantity,
                           "cost_price": _number(get("cost_price"), "cost_price"), "price": price,
                           "market_value": market_value}
    result = [positions[code] for code in sorted(positions)]
    if cash_seen:
        result.append({"code": "CASH", "name": "现金", "quantity": cash_total, "cost_price": None,
                       "price": 1.0, "market_value": cash_total})
    if not result:
        raise validation_error("holdings export is empty")
    return result


def import_snapshot(conn: sqlite3.Connection, path: Path, account: str, as_of: str, actor: str,
                    mapping: Path = DEFAULT_MAPPING, cash: float | None = None) -> tuple[dict, bool]:
    if not account.strip():
        raise validation_error("account is required")
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise validation_error("as-of must be YYYY-MM-DD") from exc
    positions = parse_export(path, mapping, cash)
    content_hash = hashlib.sha256(canonical_json(positions).encode("utf-8")).hexdigest()
    existing = conn.execute("SELECT * FROM holdings_snapshots WHERE account=? AND as_of=? AND content_hash=? "
                            "ORDER BY created_at LIMIT 1", (account, as_of, content_hash)).fetchone()
    if existing:
        return _row(existing), False
    snapshot_id = uuid.uuid4().hex
    conn.execute("INSERT INTO holdings_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (snapshot_id, account, as_of, "broker_export", canonical_json(positions), content_hash,
                  actor, utcnow_iso()))
    conn.commit()
    return show_snapshot(conn, snapshot_id), True


def _row(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["positions"] = json.loads(result.pop("positions_json"))
    return result


def show_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> dict:
    row = conn.execute("SELECT * FROM holdings_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if row is None:
        raise not_found("holdings snapshot not found", snapshot_id=snapshot_id)
    return _row(row)


def list_snapshots(conn: sqlite3.Connection, account: str | None = None) -> list[dict]:
    query = "SELECT snapshot_id, account, as_of, source, content_hash, created_by, created_at FROM holdings_snapshots"
    if account:
        rows = conn.execute(query + " WHERE account=? ORDER BY created_at DESC", (account,)).fetchall()
    else:
        rows = conn.execute(query + " ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]
