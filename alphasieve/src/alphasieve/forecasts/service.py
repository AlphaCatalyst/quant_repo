"""Forecast registration, settlement, and calibration."""

import json
import math
import sqlite3
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from alphasieve.errors import AlphaSieveError, not_found, validation_error
from alphasieve.ledger.chain import append_chained, verify_chain
from alphasieve.util import canonical_json

HASHED_COLUMNS = ("record_kind", "forecast_id", "thesis_id", "actor", "payload_json")


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal[">", ">=", "<", "<=", "between"]
    threshold: float | list[float]

    @model_validator(mode="after")
    def check_threshold(self):
        value = self.threshold
        if self.op == "between":
            if not isinstance(value, list) or len(value) != 2 or not all(math.isfinite(x) for x in value):
                raise ValueError("between requires two finite bounds")
            if value[0] > value[1]:
                raise ValueError("between bounds must be ascending")
        elif isinstance(value, list) or not math.isfinite(value):
            raise ValueError("comparison requires one finite threshold")
        return self


class ResolverParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settle_date: date
    symbol: str | None = None
    code: str | None = None


class ForecastSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    thesis_id: str | None = None
    resolver: Literal["manual", "sina_futures_close", "stock_close"]
    resolver_params: ResolverParams
    condition: Condition
    p: float = Field(gt=0, lt=1, allow_inf_nan=False)
    deadline: date
    source_of_truth: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_resolver(self):
        params = self.resolver_params
        if self.resolver == "sina_futures_close" and not params.symbol:
            raise ValueError("sina_futures_close requires resolver_params.symbol")
        if self.resolver == "stock_close" and not params.code:
            raise ValueError("stock_close requires resolver_params.code")
        if self.deadline < params.settle_date:
            raise ValueError("deadline must be on or after settle_date")
        for field in ("statement", "source_of_truth"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must not be blank")
        return self


def _event(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def _events(conn: sqlite3.Connection, forecast_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM forecast_ledger WHERE forecast_id=? ORDER BY seq", (forecast_id,))
    result = [_event(row) for row in rows]
    if not result:
        raise not_found(f"forecast {forecast_id} not found", forecast_id=forecast_id)
    return result


def _project(events: list[dict]) -> dict:
    registered = events[0]
    latest = events[-1]
    result = {
        "forecast_id": registered["forecast_id"], "thesis_id": registered["thesis_id"],
        "status": "open" if latest["record_kind"] == "registered" else latest["record_kind"],
        "spec": registered["payload"], "created_at": registered["created_at"],
    }
    if latest is not registered:
        result["resolution"] = latest["payload"]
        result["resolved_at"] = latest["created_at"]
    return result


def register_forecast(conn: sqlite3.Connection, spec: dict, actor: str) -> dict:
    try:
        parsed = ForecastSpec.model_validate(spec)
    except ValidationError as exc:
        raise validation_error("invalid forecast spec", errors=exc.errors(include_url=False)) from exc
    forecast_id = f"FC-{uuid.uuid4().hex[:12]}"
    payload = parsed.model_dump(mode="json")
    row = {"record_kind": "registered", "forecast_id": forecast_id, "thesis_id": parsed.thesis_id,
           "actor": actor, "payload_json": canonical_json(payload)}
    event = append_chained(conn, "forecast_ledger", row, HASHED_COLUMNS)
    return {"forecast_id": forecast_id, "status": "open", "seq": event["seq"], "spec": payload}


def show_forecast(conn: sqlite3.Connection, forecast_id: str) -> dict:
    events = _events(conn, forecast_id)
    return {**_project(events), "events": events}


def list_forecasts(conn: sqlite3.Connection, thesis_id: str | None = None,
                   status: str | None = None) -> dict:
    if status is not None and status not in {"open", "settled", "voided"}:
        raise validation_error("status must be open, settled, or voided")
    rows = conn.execute("SELECT * FROM forecast_ledger ORDER BY seq").fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        event = _event(row)
        grouped.setdefault(event["forecast_id"], []).append(event)
    items = [_project(events) for events in grouped.values()]
    if thesis_id is not None:
        items = [item for item in items if item["thesis_id"] == thesis_id]
    if status is not None:
        items = [item for item in items if item["status"] == status]
    return {"forecasts": items, "count": len(items)}


def _outcome(condition: dict, value: float) -> bool:
    op = condition["op"]
    threshold = condition["threshold"]
    if op == "between":
        return threshold[0] <= value <= threshold[1]
    return {">": lambda: value > threshold, ">=": lambda: value >= threshold,
            "<": lambda: value < threshold, "<=": lambda: value <= threshold}[op]()


def _provider_close(spec: dict) -> tuple[float, str]:
    resolver = spec["resolver"]
    params = spec["resolver_params"]
    settle_date = params["settle_date"]
    if resolver == "sina_futures_close":
        from alphasieve.data.providers.sina import daily_bars

        symbol = params["symbol"]
        bars = daily_bars(symbol)
        source = f"sina_futures_close:{symbol}:{settle_date}"
    else:
        from alphasieve.data.providers.baostock import BaoStockSession

        code = params["code"]
        with BaoStockSession() as session:
            bars = session.daily(code, settle_date, settle_date)
        source = f"stock_close:baostock:{code}:{settle_date}:unadjusted"
    matches = bars.loc[bars["date"].astype(str) == settle_date] if "date" in bars else bars.iloc[0:0]
    if len(matches) != 1:
        raise validation_error("settlement bar is unavailable", source=source)
    observed = float(matches.iloc[0]["close"])
    if not math.isfinite(observed):
        raise validation_error("settlement close is not finite", source=source)
    return observed, source


def settle_forecast(conn: sqlite3.Connection, forecast_id: str, actor: str,
                    value: float | None = None, source: str | None = None,
                    *, today: date | None = None) -> dict:
    events = _events(conn, forecast_id)
    current = _project(events)
    if current["status"] != "open":
        raise AlphaSieveError("CONFLICT", f"forecast is already {current['status']}")
    spec = current["spec"]
    settle_date = date.fromisoformat(spec["resolver_params"]["settle_date"])
    if (today or datetime.now(UTC).date()) < settle_date:
        raise validation_error("forecast cannot settle before settle_date", settle_date=str(settle_date))
    if spec["resolver"] == "manual":
        if value is None or not source or not source.strip():
            raise validation_error("manual settlement requires value and source")
        try:
            observed = float(value)
        except (TypeError, ValueError) as exc:
            raise validation_error("settlement value must be finite") from exc
        if not math.isfinite(observed):
            raise validation_error("settlement value must be finite")
        observed_source = source.strip()
    else:
        if value is not None or source is not None:
            raise validation_error("provider settlement does not accept a caller value or source")
        observed, observed_source = _provider_close(spec)
    outcome = _outcome(spec["condition"], observed)
    payload = {"observed_value": observed, "source": observed_source, "outcome": outcome}
    row = {"record_kind": "settled", "forecast_id": forecast_id, "thesis_id": current["thesis_id"],
           "actor": actor, "payload_json": canonical_json(payload)}
    event = append_chained(conn, "forecast_ledger", row, HASHED_COLUMNS)
    return {"forecast_id": forecast_id, "status": "settled", "seq": event["seq"], **payload}


def void_forecast(conn: sqlite3.Connection, forecast_id: str, reason: str, actor: str) -> dict:
    if not reason.strip():
        raise validation_error("void reason is required")
    current = _project(_events(conn, forecast_id))
    if current["status"] != "open":
        raise AlphaSieveError("CONFLICT", f"forecast is already {current['status']}")
    row = {"record_kind": "voided", "forecast_id": forecast_id, "thesis_id": current["thesis_id"],
           "actor": actor, "payload_json": canonical_json({"reason": reason.strip()})}
    event = append_chained(conn, "forecast_ledger", row, HASHED_COLUMNS)
    return {"forecast_id": forecast_id, "status": "voided", "seq": event["seq"]}


def score_forecasts(conn: sqlite3.Connection, thesis_id: str | None = None) -> dict:
    forecasts = list_forecasts(conn, thesis_id=thesis_id)["forecasts"]
    settled = [item for item in forecasts if item["status"] == "settled"]
    buckets = [{"range": f"{i / 10:.1f}-{(i + 1) / 10:.1f}", "count": 0,
                "observed_frequency": None} for i in range(10)]
    totals = [0] * 10
    by_thesis: dict[str, list[float]] = {}
    scores = []
    for item in settled:
        p = item["spec"]["p"]
        outcome = int(item["resolution"]["outcome"])
        score = (p - outcome) ** 2
        scores.append(score)
        if item["thesis_id"] is not None:
            by_thesis.setdefault(item["thesis_id"], []).append(score)
        index = min(int(p * 10), 9)
        buckets[index]["count"] += 1
        totals[index] += outcome
    for bucket, total in zip(buckets, totals, strict=True):
        if bucket["count"]:
            bucket["observed_frequency"] = total / bucket["count"]
    return {"count": len(scores), "brier_score": sum(scores) / len(scores) if scores else None,
            "by_thesis": {key: {"count": len(values), "brier_score": sum(values) / len(values)}
                          for key, values in sorted(by_thesis.items())}, "calibration": buckets}


def verify_forecasts(conn: sqlite3.Connection) -> dict:
    return verify_chain(conn, "forecast_ledger", HASHED_COLUMNS)
