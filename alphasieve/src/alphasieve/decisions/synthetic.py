"""PD-1: synthetic concentrated accounts drawn from point-in-time stock pools."""

import warnings
from dataclasses import asdict, dataclass

import numpy as np

from alphasieve.decisions.bars import Bars

POOLS = ("all", "large", "growth")


@dataclass(frozen=True)
class AccountSpec:
    pool: str = "all"
    accounts: int = 10000
    start_from: str = "2007-01-01"
    horizon_days: int = 244
    holdings: tuple[int, ...] = (2, 3, 4, 5)
    dirichlet_alpha: float = 1.0
    capital: float = 200_000.0
    seed: int = 20261007

    def as_dict(self) -> dict:
        return {**asdict(self), "holdings": list(self.holdings)}


@dataclass(frozen=True)
class Accounts:
    spec: AccountSpec
    start: np.ndarray  # int [A], index into bars.dates of the day-0 close
    names: np.ndarray  # int [A, K], column into bars.codes, -1 when unused
    weights: np.ndarray  # float [A, K], zero when unused

    @property
    def mask(self) -> np.ndarray:
        return self.names >= 0


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    filled = np.nan_to_num(values.astype(np.float64))
    count = np.isfinite(values).astype(np.float64)
    csum, ccount = np.cumsum(filled, axis=0), np.cumsum(count, axis=0)
    csum[window:] = csum[window:] - csum[:-window]
    ccount[window:] = ccount[window:] - ccount[:-window]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ccount >= window // 2, csum / ccount, np.nan)


def pool_masks(bars: Bars, pool: str) -> np.ndarray:
    """Membership [T, N] using only data available at each close."""
    if pool not in POOLS:
        raise ValueError(f"pool must be one of {POOLS}")
    eligible = ((bars["days_listed"] >= 250) & ~bars["is_st"] & ~bars["is_suspended"]
                & np.isfinite(bars["close"]))
    amount = np.where(eligible, _rolling_mean(bars["amount"], 20), np.nan)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        cut = np.nanquantile(np.where(np.isfinite(amount), amount, np.nan), 0.2, axis=1, keepdims=True)
        eligible &= amount > cut
    if pool == "all":
        return eligible
    if pool == "large":
        mv = np.where(eligible, bars["circ_mv"], -np.inf)
        rank = np.argsort(np.argsort(-mv, axis=1), axis=1)
        return eligible & (rank < 800)
    codes = bars.codes.astype(str)
    board = np.zeros(len(codes), dtype=bool)
    for prefix in ("sz.300", "sz.301", "sh.688"):
        board |= np.char.startswith(codes, prefix)
    turnover = np.where(eligible, _rolling_mean(bars["turnover_rate"], 20), np.nan)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        high = turnover > np.nanquantile(turnover, 0.8, axis=1, keepdims=True)
    return eligible & (board[None, :] | high)


def draw_accounts(bars: Bars, spec: AccountSpec, masks: np.ndarray | None = None) -> Accounts:
    rng = np.random.default_rng(spec.seed)
    masks = pool_masks(bars, spec.pool) if masks is None else masks
    first = int(np.searchsorted(bars.dates, spec.start_from))
    last = len(bars.dates) - 1 - spec.horizon_days
    candidates = np.array([t for t in range(first, last + 1) if masks[t].sum() >= max(spec.holdings) * 4])
    k_max = max(spec.holdings)
    start = rng.choice(candidates, spec.accounts)
    names = np.full((spec.accounts, k_max), -1, dtype=np.int64)
    weights = np.zeros((spec.accounts, k_max))
    for a, t in enumerate(start):
        k = int(rng.choice(spec.holdings))
        names[a, :k] = rng.choice(np.flatnonzero(masks[t]), k, replace=False)
        weights[a, :k] = rng.dirichlet(np.full(k, spec.dirichlet_alpha))
    return Accounts(spec, start, names, weights)
