"""Wide all-A daily matrices for synthetic-account work, read only up to the dev end."""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.config import Settings, load_config
from alphasieve.data.panel import _stock_frame
from alphasieve.data.sync import universe_codes
from alphasieve.errors import validation_error

UNIVERSE = "ashare_all"
HISTORY_START = "2005-01-01"
CACHE_VERSION = 1
FIELDS = ("open", "close", "open_raw", "close_raw", "amount", "circ_mv", "turnover_rate", "pe_ttm", "pb_mrq",
          "days_listed")
FLAGS = ("tradable_buy", "tradable_sell", "is_st", "is_suspended")


@dataclass(frozen=True)
class Bars:
    dates: np.ndarray  # str, trading days
    codes: np.ndarray  # str
    values: dict[str, np.ndarray]  # field -> float32 [T, N], NaN where not listed / no data
    flags: dict[str, np.ndarray]  # flag -> bool [T, N]

    def __getitem__(self, name: str) -> np.ndarray:
        return self.values[name] if name in self.values else self.flags[name]


def dev_end(settings: Settings) -> str:
    return load_config(settings, "splits")["dev"]["end"]


_WORKER: dict = {}


def _init(root: str, cal_pos: dict[str, int], end: str) -> None:
    _WORKER.update(root=Path(root), cal_pos=cal_pos, end=end)


def _one(job: tuple[str, str]) -> pd.DataFrame | None:
    code, ipo = job
    frame = _stock_frame(code, _WORKER["root"], _WORKER["cal_pos"], ipo, _WORKER["end"], HISTORY_START)
    if frame is None:
        return None
    return frame[["date", "code", *FIELDS, *FLAGS]]


def load_bars(settings: Settings, end: str | None = None, workers: int = 8) -> Bars:
    limit = dev_end(settings)
    end = end or limit
    if end > limit:
        raise validation_error("synthetic-account data is limited to the dev tier", end=end, dev_end=limit)
    cache = settings.cache_dir / "decisions" / f"bars-{UNIVERSE}-{end}-v{CACHE_VERSION}.npz"
    if cache.is_file():
        data = np.load(cache, allow_pickle=False)
        return Bars(data["dates"], data["codes"], {f: data[f] for f in FIELDS}, {f: data[f] for f in FLAGS})
    root = settings.raw_dir / "baostock_all"
    cal = pd.read_parquet(root / "trade_dates.parquet")
    dates = sorted(d for d in cal.loc[cal["is_trading_day"] == 1, "calendar_date"] if HISTORY_START <= d <= end)
    cal_pos = {d: i for i, d in enumerate(dates)}
    basic = pd.read_parquet(root / "stock_basic.parquet").set_index("code")
    codes = universe_codes(settings, UNIVERSE)
    jobs = [(c, (basic.at[c, "ipoDate"] if c in basic.index else "") or HISTORY_START) for c in codes]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(root), cal_pos, end)) as pool:
        frames = [f for f in pool.map(_one, jobs, chunksize=32) if f is not None and not f.empty]
    kept = [f["code"].iloc[0] for f in frames]
    t, n = len(dates), len(kept)
    values = {f: np.full((t, n), np.nan, dtype=np.float32) for f in FIELDS}
    flags = {f: np.zeros((t, n), dtype=bool) for f in FLAGS}
    for j, frame in enumerate(frames):
        rows = frame["date"].map(cal_pos).to_numpy()
        for f in FIELDS:
            values[f][rows, j] = frame[f].to_numpy(dtype=np.float32)
        for f in FLAGS:
            flags[f][rows, j] = frame[f].to_numpy(dtype=bool)
    bars = Bars(np.array(dates), np.array(kept), values, flags)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(cache.stem + ".tmp.npz")
    np.savez(tmp, dates=bars.dates, codes=bars.codes, **values, **flags)
    tmp.replace(cache)
    return bars
