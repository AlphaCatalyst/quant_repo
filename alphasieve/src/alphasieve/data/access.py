import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError, not_found, permission_denied

# float64 by default (exact parity with the v1 evaluation); float32 halves the wide-table cache of a worker.
WIDE_DTYPE = np.float32 if os.environ.get("ALPHASIEVE_WIDE_DTYPE") == "float32" else np.float64
TIER_READERS = {"dev": ("agent", "human", "system"), "holdout": ("system",), "fresh": ("system",)}


def check_tier_access(role: str, tier: str) -> None:
    if role not in TIER_READERS.get(tier, ()):
        raise permission_denied(f"role {role} may not read {tier} data", tier=tier)


class Panel:
    def __init__(self, long: pd.DataFrame, meta: dict, benchmark: pd.DataFrame | None = None):
        self.long = long
        self.meta = meta
        self.benchmark = benchmark
        self.dates = pd.DatetimeIndex(sorted(long["date"].unique()))
        self.codes = sorted(long["code"].unique())
        self._wide: dict[str, pd.DataFrame] = {}
        self._indexed = long.set_index(["date", "code"])
        self._row = self.dates.get_indexer(long["date"])
        self._col = pd.Index(self.codes).get_indexer(long["code"])
        self._unique = not long.duplicated(["date", "code"]).any()

    @property
    def tier(self) -> str:
        return self.meta["tier"]

    @property
    def signature(self) -> str:
        return self.meta["signature"]

    @property
    def window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return pd.Timestamp(self.meta["window"]["start"]), pd.Timestamp(self.meta["window"]["end"])

    def has(self, field: str) -> bool:
        return field in self.long.columns

    def wide(self, field: str) -> pd.DataFrame:
        if field not in self._wide:
            if field not in self.long.columns:
                raise not_found(f"field {field} not in panel")
            column = self.long[field]
            numeric = pd.api.types.is_numeric_dtype(column) or pd.api.types.is_bool_dtype(column)
            if numeric and self._unique:
                grid = np.full((len(self.dates), len(self.codes)), np.nan, dtype=WIDE_DTYPE)
                grid[self._row, self._col] = column.to_numpy(dtype=float, na_value=np.nan)
                self._wide[field] = pd.DataFrame(grid, index=self.dates, columns=self.codes)
            else:
                series = self._indexed[field]
                if series.dtype == bool or str(series.dtype) == "boolean":
                    series = series.astype(float)
                self._wide[field] = series.unstack("code").reindex(index=self.dates, columns=self.codes)
        return self._wide[field]

    def set_wide(self, field: str, frame: pd.DataFrame) -> None:
        self._wide[field] = frame.reindex(index=self.dates, columns=self.codes)

    def mask(self, field: str) -> pd.DataFrame:
        return self.wide(field).fillna(0).astype(bool)

    def window_mask(self) -> pd.Series:
        start, end = self.window
        return pd.Series((self.dates >= start) & (self.dates <= end), index=self.dates)

    def industry(self) -> pd.Series:
        last = self.long.drop_duplicates("code", keep="last").set_index("code")["industry"]
        return last.reindex(self.codes).fillna("unknown")

    def truncated(self, last_date) -> "Panel":
        long = self.long[self.long["date"] <= pd.Timestamp(last_date)]
        return Panel(long.reset_index(drop=True), dict(self.meta), self.benchmark)


@lru_cache(maxsize=4)
def _read(panel_path: str, mtime: float) -> tuple[pd.DataFrame, dict, pd.DataFrame | None]:
    path = Path(panel_path)
    long = pd.read_parquet(path)
    meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
    bench_path = path.parent / "benchmark.parquet"
    bench = pd.read_parquet(bench_path) if bench_path.exists() else None
    return long, meta, bench


def load_panel(settings: Settings, tier: str = "dev", role: str | None = None, universe: str | None = None) -> Panel:
    role = role or settings.role
    check_tier_access(role, tier)
    path = settings.panel_dir(tier, universe) / "panel.parquet"
    if not path.exists():
        raise AlphaSieveError("NOT_FOUND", f"{tier} panel for universe {universe or 'csi800'} not built;"
                              " run 'alphasieve data build-panel'")
    long, meta, bench = _read(str(path), path.stat().st_mtime)
    return Panel(long, meta, bench)


def read_meta(settings: Settings, tier: str, universe: str | None = None) -> dict | None:
    path = settings.panel_dir(tier, universe) / "meta.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError):
        return None
