from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from alphasieve.training.forward import fit_asof, lock_selection, score_asof
from alphasieve.training.samples import SampleTable


def _case():
    dates = pd.bdate_range("2026-01-01", periods=52)
    group = np.repeat(np.arange(len(dates)), 12)
    rng = np.random.default_rng(4)
    x = rng.normal(size=(len(group), 2)).astype("float32")
    table = SampleTable(date_pos=group, code_pos=np.tile(np.arange(12), len(dates)), X=x,
                        feature_names=["a", "b"])
    table.Y[1] = x[:, 0] * 0.2 + rng.normal(scale=0.01, size=len(group))
    table.label_end[1] = group + 2
    table.predict = np.ones(len(group), bool)
    task = SimpleNamespace(score_source=None, label=SimpleNamespace(horizons=[1]),
                           split=SimpleNamespace(retrain="monthly", window="expanding", train_years=1),
                           sample=SimpleNamespace(purge_days=3, min_train_rows=50),
                           search=SimpleNamespace(seeds=[0, 1]),
                           ensemble=SimpleNamespace(horizon_weights={1: 1.0}))
    return dates, table, task


def test_forward_purge_monthly_and_future_label_independence():
    dates, table, task = _case()
    chosen = {"1": {"family": "ridge", "params": {"alpha": 1.0}}}
    first = fit_asof(table, dates, dates[23], task, chosen, ["a", "b"])
    assert first["max_train_date"] == {1: str(dates[19].date())}
    assert first["max_label_end"] == {1: str(dates[21].date())}
    score = score_asof(first, table, dates[23])
    table.Y[1][table.date_pos >= 20] = 1e9
    again = fit_asof(table, dates, dates[23], task, chosen, ["a", "b"])
    np.testing.assert_allclose(score, score_asof(again, table, dates[23]), equal_nan=True)
    assert first["model_digest"] == again["model_digest"]
    assert fit_asof(table, dates, dates[24], task, chosen, ["a", "b"], previous=first) is first
    with pytest.raises(Exception, match="monthly forward model refit"):
        score_asof(first, table, dates[45])
    following = fit_asof(table, dates, dates[45], task, chosen, ["a", "b"], previous=first)
    assert following["asof"] == dates[45] and following["model_digest"] != first["model_digest"]


def test_forward_requires_locked_selection_and_features():
    dates, table, task = _case()
    with pytest.raises(Exception, match="source selection"):
        lock_selection({}, [1])
    assert lock_selection({"2024/h1": {"chosen": {"family": "ridge", "params": {"alpha": 2}}}}, [1]) \
        == {1: {"family": "ridge", "params": {"alpha": 2}}}
    with pytest.raises(Exception, match="locked order"):
        fit_asof(table, dates, dates[23], task, {1: {"family": "ridge", "params": {}}}, ["b", "a"])
    with pytest.raises(Exception, match="every horizon"):
        fit_asof(table, dates, dates[23], task, {}, ["a", "b"])


def test_score_source_rejected_before_samples_are_accessed():
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError(f"sample access before score_source rejection: {name}")

    dates, _, task = _case()
    bundle = {"features": {"score_source": {"run": "/nonexistent/source"}}}
    with pytest.raises(Exception, match="score_source"):
        fit_asof(Unreadable(), dates, dates[23], task, {}, ["a"], bundle=bundle)
    task.score_source = object()
    with pytest.raises(Exception, match="score_source"):
        fit_asof(Unreadable(), dates, dates[23], task, {}, ["a"])
