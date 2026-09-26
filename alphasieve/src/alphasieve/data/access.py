import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError, not_found, permission_denied

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


def load_panel(settings: Settings, tier: str = "dev", role: str | None = None) -> Panel:
    role = role or settings.role
    check_tier_access(role, tier)
    path = settings.panel_dir(tier) / "panel.parquet"
    if not path.exists():
        raise AlphaSieveError("NOT_FOUND", f"{tier} panel not built; run 'alphasieve data build-panel'")
    long, meta, bench = _read(str(path), path.stat().st_mtime)
    return Panel(long, meta, bench)


def read_meta(settings: Settings, tier: str) -> dict | None:
    path = settings.panel_dir(tier) / "meta.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError):
        return None
