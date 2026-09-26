import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.evaluation import metrics as M
from alphasieve.evaluation.marginal import marginal_contribution
from alphasieve.evaluation.tradable import long_only_excess


class EvalInputs:
    def __init__(self, panel: Panel, horizon: int):
        self.panel = panel
        self.horizon = horizon
        window = panel.window_mask()
        self.window = window
        universe = panel.mask("in_universe")
        self.universe = universe & np.broadcast_to(window.to_numpy()[:, None], universe.shape)
        self.label = panel.wide(f"label_{horizon}d")
        self.valid = self.universe & self.label.notna()
        self._groups = None
        self._size = None

    @property
    def groups(self) -> pd.Series:
        if self._groups is None:
            self._groups = self.panel.industry()
        return self._groups

    @property
    def size(self) -> pd.DataFrame:
        if self._size is None:
            mv = self.panel.wide("circ_mv")
            self._size = np.log(mv.where(mv > 0))
        return self._size


def l1_metrics(factor: pd.DataFrame, inp: EvalInputs, library: dict[str, pd.DataFrame]) -> dict:
    ic = M.rank_corr_series(factor, inp.label, inp.valid)
    out = M.summarize_ic(ic)
    out["coverage"] = M.coverage(factor, inp.universe)
    out["quantiles"] = M.quantile_returns(factor, inp.label, inp.valid)
    out["turnover_proxy"] = M.turnover_proxy(factor, inp.universe)
    out["library"] = M.library_correlations(factor, library, inp.universe)
    out["_ic_series"] = ic
    return out


def l2_metrics(factor: pd.DataFrame, inp: EvalInputs, baseline: dict[str, pd.DataFrame], costs: dict,
               subwindows: int, ic_series: pd.Series) -> dict:
    p = inp.panel
    b2 = costs["b2"]
    return {
        "subwindow_ic": M.subwindow_ics(ic_series, subwindows),
        "neutral": M.neutralized_ic(factor, inp.label, inp.valid, inp.groups, inp.size),
        "tradable": long_only_excess(
            factor, inp.universe, p.wide("open"), p.mask("tradable_buy"), p.mask("tradable_sell"), inp.window,
            b2["rebalance_every"], b2["top_fraction"], b2["one_way_cost"],
        ),
        "marginal": marginal_contribution(factor, baseline, inp.label, inp.valid, inp.window, inp.horizon),
    }


def public_metrics(metrics: dict) -> dict:
    return {k: v for k, v in metrics.items() if not k.startswith("_")}
