import pandas as pd

from alphasieve.data import cb_sync
from alphasieve.data.providers import westock
from alphasieve.state import connect


def test_cb_sync_snapshots_and_resume(settings, monkeypatch):
    universe = pd.DataFrame([
        {"code": "sz.127045", "name": "牧原转债", "list_date": "2021-09-10", "convert_price": 43.32},
        {"code": "sz.404001", "name": "旧债", "list_date": "2007-01-01", "convert_price": None},
    ])
    monkeypatch.setattr(westock, "bond_universe", lambda: universe)
    monkeypatch.setattr(westock, "bond_quote", lambda codes: pd.DataFrame(
        [{"code": "sz.127045", "time": "2026-10-02", "price": 118.0, "bond_convert_price": 43.32}]))
    monkeypatch.setattr(westock, "bond_detail", lambda codes: {
        "terms": pd.DataFrame([{"code": code, "convertPrice": 43.32} for code in codes]),
        "coupons": pd.DataFrame([{"code": code, "CouponRate": 1.0} for code in codes]),
        **{name: pd.DataFrame(columns=["code"]) for name in ("puts", "calls", "revisions", "cashflows")},
    })
    calls = []

    def daily(codes, start, end):
        calls.append((codes[0], start, end))
        return pd.DataFrame([{"code": codes[0], "date": start, "last": 100.0}])

    monkeypatch.setattr(westock, "bond_daily", daily)
    conn = connect(settings.state_db)
    day = "2026-10-02"
    assert cb_sync.sync_cb_universe(settings, conn, day)["rows"] == 2
    assert cb_sync.sync_cb_universe(settings, conn, day)["existing"]
    assert cb_sync.sync_cb_terms(settings, conn, day)["tables"]["coupons"] == 2
    assert cb_sync.sync_cb_quote(settings, conn, day)["rows"] == 1
    assert cb_sync.sync_cb_quote(settings, conn, day)["existing"]
    assert cb_sync.sync_cb_daily(settings, conn, day, full=False, day=day)["codes"] == 1
    assert calls == [("sz.127045", "2026-09-18", day)]
    calls.clear()
    result = cb_sync.sync_cb_daily(settings, conn, day, full=True, day=day)
    assert result["codes"] == 2
    assert ("sz.127045", "2026-09-19", day) in calls
    assert ("sz.404001", "2007-01-01", day) in calls
    path = cb_sync._root(settings) / "cb_daily" / "sz127045.parquet"
    assert len(pd.read_parquet(path)) == 2


def test_cb_terms_isolates_one_bad_bond_in_batch(settings, monkeypatch):
    monkeypatch.setattr(westock, "bond_universe", lambda: pd.DataFrame([
        {"code": f"sz.{n:06d}", "list_date": "2020-01-01"} for n in (1, 2, 3, 4)]))

    def detail(codes):
        if len(codes) > 1:
            raise westock.WestockError("batch rejected")
        if codes[0] == "sz.000004":
            raise westock.WestockError("unsupported old bond")
        return {"terms": pd.DataFrame([{"code": codes[0], "issuer": "x"}]),
                **{name: pd.DataFrame(columns=["code"]) for name in
                   ("coupons", "puts", "calls", "revisions", "cashflows")}}

    monkeypatch.setattr(westock, "bond_detail", detail)
    conn = connect(settings.state_db)
    cb_sync.sync_cb_universe(settings, conn, "2026-10-03")
    result = cb_sync.sync_cb_terms(settings, conn, "2026-10-03")
    assert result["tables"]["terms"] == 3
    assert [error["item"] for error in result["errors"]] == ["sz.000004"]
    assert len(pd.read_parquet(cb_sync._root(settings) / "cb_terms" / "2026-10-03" / "terms.parquet")) == 3
