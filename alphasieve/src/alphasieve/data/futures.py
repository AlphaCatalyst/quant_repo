"""CFFEX index futures for mandate D (docs/18 §6, docs/20 §4): Sina contract bars and a hedge-leg series.

Coverage of the free source decides what can be claimed:
- from 2019-04 every listed contract has its own bars, so the held contract, its return, basis and rolls are exact;
- 2017-01-17 .. 2019-03 only Sina's ``IC0`` splice exists: returns come from it and the roll days are inferred;
- before 2017 there is no futures data at all and D falls back to the index proxy (``source == "none"``).
"""

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.sync import _write_parquet, record_snapshot

PRODUCTS = {"IC": "zz500", "IF": "hs300"}
FIRST_CONTRACT = "1903"
CONTINUOUS = "0"
SETTLED_BASIS = 0.002       # on its settlement day the expiring contract trades within this of the index


def futures_root(settings: Settings):
    return settings.raw_dir / "sina" / "futures"


def contract_codes(product: str, end: str, first: str = FIRST_CONTRACT) -> list[str]:
    """Every monthly contract code from ``first`` (yymm) to twelve months past ``end``."""
    months = pd.period_range(pd.Period(f"20{first[:2]}-{first[2:]}", "M"), pd.Period(end, "M") + 12, freq="M")
    return [f"{product}{p.year % 100:02d}{p.month:02d}" for p in months]


def sync_futures(settings: Settings, conn, end: str, products: tuple[str, ...] = ("IC",)) -> dict:
    """Fetch the continuous series and every contract; expired contracts already on disk are not refetched."""
    from alphasieve.data.providers.sina import SinaError, daily_bars

    out, errors, paths = {}, [], []
    stale = (pd.Timestamp(end) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    for product in products:
        root = futures_root(settings) / product
        got = {}
        for symbol in [product + CONTINUOUS, *contract_codes(product, end)]:
            path = root / f"{symbol}.parquet"
            if path.exists() and symbol != product + CONTINUOUS:
                have = pd.read_parquet(path, columns=["date"])["date"]
                if len(have) and have.max() < stale:
                    got[symbol] = {"rows": int(len(have)), "cached": True}
                    paths.append(path)
                    continue
            try:
                df = daily_bars(symbol)
            except SinaError as exc:
                errors.append({"symbol": symbol, "error": str(exc)[:200]})
                continue
            if df.empty:
                continue
            _write_parquet(df, path)
            paths.append(path)
            got[symbol] = {"rows": int(len(df)), "first": df["date"].iloc[0], "last": df["date"].iloc[-1]}
        out[product] = got
    snap = record_snapshot(conn, "sina:futures", {"end": end, "products": list(products)}, paths,
                           sum(v["rows"] for g in out.values() for v in g.values()), "sina")
    return {"products": {p: {"symbols": len(g), "detail": g} for p, g in out.items()}, "errors": errors,
            "snapshot": snap}


def load_contracts(settings: Settings, product: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wide close and open-interest frames (date x contract) of the single contracts."""
    root = futures_root(settings) / product
    frames = []
    for path in sorted(root.glob(f"{product}[0-9][0-9][0-9][0-9].parquet")):
        df = pd.read_parquet(path)
        df["contract"] = path.stem
        frames.append(df)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    long = pd.concat(frames, ignore_index=True)
    long["date"] = pd.to_datetime(long["date"])
    close = long.pivot(index="date", columns="contract", values="close").sort_index()
    oi = long.pivot(index="date", columns="contract", values="oi").reindex_like(close)
    return close, oi


def load_continuous(settings: Settings, product: str) -> pd.DataFrame:
    path = futures_root(settings) / product / f"{product}{CONTINUOUS}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def third_friday(month: pd.Period) -> pd.Timestamp:
    first = month.start_time
    return first + pd.Timedelta(days=(4 - first.weekday()) % 7 + 14)


class _Calendar:
    """Trading-day positions on ``dates``, extended past the last date by business days."""

    def __init__(self, dates: pd.DatetimeIndex):
        self.dates = dates

    def settlement(self, month: pd.Period) -> pd.Timestamp:
        friday = third_friday(month)
        pos = self.dates.searchsorted(friday)
        return self.dates[pos] if pos < len(self.dates) else friday

    def pos(self, day: pd.Timestamp) -> int:
        if day <= self.dates[-1]:
            return int(self.dates.searchsorted(day))
        return len(self.dates) - 1 + int(np.busday_count(self.dates[-1].date(), day.date()))


def _continuous_segment(out: pd.DataFrame, f: pd.Series, spot: pd.Series, seg: pd.Series,
                        settlements: pd.DatetimeIndex, cal: _Calendar, settled_basis: float) -> None:
    idx = np.flatnonzero(seg.to_numpy())
    if len(idx) < 2:
        return
    dates = cal.dates
    ret = f / f.shift(1) - 1
    basis = f / spot - 1
    for e in settlements[settlements <= dates[-1]]:
        i = cal.pos(e)
        if not seg.iloc[i] or i + 1 >= len(dates):
            continue
        # IC0 still quotes the expiring contract on its settlement day once its basis has converged
        if abs(basis.iloc[i]) > settled_basis:
            ret.iloc[i] = spot.iloc[i] / f.iloc[i - 1] - 1
        else:
            ret.iloc[i + 1] = spot.iloc[i + 1] / spot.iloc[i] - 1
        out.iloc[i, out.columns.get_loc("roll")] = True
    rows = dates[idx]
    out.loc[rows[1:], "fut_ret"] = ret.loc[rows[1:]]
    out.loc[rows, "basis"] = basis.loc[rows]
    out.loc[rows, "source"] = "continuous"
    nxt = settlements.searchsorted(rows, side="right")
    out.loc[rows, "days_to_expiry"] = [cal.pos(settlements[k]) - p for k, p in zip(nxt, idx, strict=True)]


def _contract_segment(out: pd.DataFrame, close: pd.DataFrame, expiry: pd.Series, spot: pd.Series,
                      cal: _Calendar, start: int) -> None:
    dates = cal.dates
    held_prev = None
    for i in range(start, len(dates)):
        d = dates[i]
        live = expiry[expiry > d]
        if held_prev is not None:
            out.iat[i, out.columns.get_loc("fut_ret")] = close.at[d, held_prev] / close.at[dates[i - 1], held_prev] - 1
        held_prev = None
        if live.empty or not np.isfinite(spot.iloc[i]) or not np.isfinite(close.at[d, live.index[0]]):
            continue
        held = live.index[0]
        out.loc[d, ["basis", "days_to_expiry", "roll", "source"]] = [
            close.at[d, held] / spot.iloc[i] - 1, cal.pos(expiry[held]) - i, bool((expiry == d).any()), "contract"]
        held_prev = held


def hedge_leg(settings: Settings, product: str, index_close: pd.Series,
              settled_basis: float = SETTLED_BASIS) -> pd.DataFrame:
    """Daily return of a position in ``product`` that holds the front-month contract to its cash settlement and
    opens the next contract at the settlement-day close, on the dates of ``index_close`` (nothing later is read).

    Columns: ``fut_ret`` (one long contract's return; a short hedge earns minus this), ``basis`` (contract held
    after the close / index - 1), ``days_to_expiry`` (trading days), ``roll`` (a new contract was opened at this
    close) and ``source``: ``contract`` exact, ``continuous`` from the IC0 splice with inferred rolls, ``none``.
    """
    dates = pd.DatetimeIndex(index_close.index)
    spot = index_close.astype(float)
    cal = _Calendar(dates)
    settle = {m: cal.settlement(m) for m in pd.period_range(dates[0], dates[-1] + pd.DateOffset(months=13), freq="M")}
    settlements = pd.DatetimeIndex(sorted(settle.values()))
    out = pd.DataFrame({"fut_ret": np.nan, "basis": np.nan, "days_to_expiry": np.nan, "roll": False,
                        "source": "none"}, index=dates)
    close, _ = load_contracts(settings, product)
    exact_from = None
    if not close.empty:
        close = close.reindex(dates).dropna(axis=1, how="all")
        expiry = pd.Series({c: settle[pd.Period(f"20{c[-4:-2]}-{c[-2:]}", "M")] for c in close.columns})
        expiry = expiry.sort_values()
        exact_from = _first_complete_roll(close, expiry, settlements, cal)
    ic0 = load_continuous(settings, product)
    if not ic0.empty:
        f = ic0["close"].reindex(dates)
        seg = f.notna() & spot.notna()
        if exact_from is not None:
            seg &= np.arange(len(dates)) <= exact_from
        _continuous_segment(out, f, spot, seg, settlements, cal, settled_basis)
    if exact_from is not None:
        _contract_segment(out, close, expiry, spot, cal, exact_from)
    return out


def _first_complete_roll(close: pd.DataFrame, expiry: pd.Series, settlements: pd.DatetimeIndex,
                         cal: _Calendar) -> int | None:
    """Position of the first settlement day from which every front contract has a price on every day it is held
    (Sina's early contracts are fragments)."""
    first = None
    for e in settlements[(settlements >= close.index[0]) & (settlements <= cal.dates[-1])]:
        live = expiry[expiry > e]
        if live.empty:
            break
        front = live.index[0]
        held = close.loc[e:min(live.iloc[0], cal.dates[-1]), front]
        if held.notna().all():
            first = cal.pos(e) if first is None else first
        else:
            first = None
    return first


def read_hedge_leg(settings: Settings, tier: str, role: str, product: str,
                   universe: str | None = None) -> pd.DataFrame:
    from alphasieve.data.access import check_tier_access
    from alphasieve.errors import AlphaSieveError

    check_tier_access(role, tier)
    path = settings.panel_dir(tier, universe) / f"futures_{product}.parquet"
    if not path.exists():
        raise AlphaSieveError("NOT_FOUND", f"no {product} hedge leg for the {tier} tier; run 'alphasieve data"
                              f" build-futures --tiers {tier}'")
    return pd.read_parquet(path).set_index("date")


def build_futures_tiers(settings: Settings, product: str = "IC", universe: str | None = None,
                        tiers: tuple[str, ...] = ("dev", "holdout")) -> dict:
    """Write ``futures_<product>.parquet`` next to each tier's panel, cut at that tier's benchmark dates."""
    results = {}
    for tier in tiers:
        out_dir = settings.panel_dir(tier, universe)
        bench = pd.read_parquet(out_dir / "benchmark.parquet")
        spot = bench.assign(date=pd.to_datetime(bench["date"])).set_index("date")[f"{PRODUCTS[product]}_close"]
        leg = hedge_leg(settings, product, spot)
        path = out_dir / f"futures_{product}.parquet"
        _write_parquet(leg.rename_axis("date").reset_index(), path)
        if tier != "dev":
            path.chmod(0o600)
        by_source = leg.groupby("source").apply(lambda g: [str(g.index.min().date()), str(g.index.max().date())])
        results[tier] = {"path": str(path), "rows": int(len(leg)), "rolls": int(leg["roll"].sum()),
                         "coverage": by_source.to_dict()}
    return results
