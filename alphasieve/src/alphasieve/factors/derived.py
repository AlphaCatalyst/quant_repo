"""Derived terminal variables (version dv1), computed by the system from panel fields."""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel

DERIVED_VERSION = "dv1"


def _excess_ret(p: Panel) -> pd.DataFrame:
    ret = p.wide("ret_1d")
    universe = p.mask("in_universe")
    bench = ret.where(universe).mean(axis=1)
    return ret.sub(bench, axis=0)


def _amihud(p: Panel) -> pd.DataFrame:
    amount = p.wide("amount")
    illiq = p.wide("ret_1d").abs() / (amount.where(amount > 0) / 1e8)
    return illiq.replace([np.inf, -np.inf], np.nan).rolling(20, min_periods=20).mean()


def _vwap_dev(p: Panel) -> pd.DataFrame:
    return p.wide("close") / p.wide("vwap") - 1


def _overnight(p: Panel) -> pd.DataFrame:
    return p.wide("open") / p.wide("close").shift(1) - 1


def _intraday(p: Panel) -> pd.DataFrame:
    return p.wide("close") / p.wide("open") - 1


DERIVED = {
    "excess_ret_1d": _excess_ret,
    "amihud_20d": _amihud,
    "vwap_dev": _vwap_dev,
    "overnight_ret": _overnight,
    "intraday_ret": _intraday,
}


def terminal(panel: Panel, name: str) -> pd.DataFrame:
    if name in DERIVED and not panel.has(name):
        key = f"__derived__{name}"
        if key not in panel._wide:
            panel._wide[key] = DERIVED[name](panel)
        return panel._wide[key]
    return panel.wide(name)
