from dataclasses import replace

import pytest

from alphasieve import tracking


def test_flatten_keeps_finite_numbers_only():
    flat = tracking.flatten({"a": 1, "b": {"c": 0.5, "d": float("nan"), "e": "text", "f": True}, "g": [1, 2]})
    assert flat == {"a": 1, "b/c": 0.5, "b/f": 1}


def test_record_is_a_noop_unless_enabled(panel_settings, monkeypatch):
    monkeypatch.delenv("ALPHASIEVE_TRACKING", raising=False)
    assert tracking.record("gate calibrate", {}, {"status": "ok", "data": {"x": 1}}, panel_settings) is None


@pytest.fixture
def offline(monkeypatch, tmp_path):
    pytest.importorskip("wandb")
    monkeypatch.setenv("ALPHASIEVE_TRACKING", "runlab")
    monkeypatch.setenv("WANDB_API_KEY", "x" * 40)
    monkeypatch.setenv("WANDB_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("WANDB_MODE", "offline")
    monkeypatch.setenv("WANDB_DIR", str(tmp_path))
    monkeypatch.setenv("WANDB_SILENT", "true")
    return tmp_path


def test_record_offline_and_never_for_holdout_or_agent(panel_settings, offline):
    envelope = {"status": "ok", "data": {"null": {"l1_pass_rate": 0.01}, "seeds": {"n": 18}}}
    assert tracking.record("gate calibrate", {"random": 10}, envelope, panel_settings)
    assert list(offline.rglob("*.wandb"))
    assert tracking.record("holdout approve", {}, envelope, panel_settings) is None
    assert tracking.record("gate calibrate", {}, envelope, replace(panel_settings, role="agent")) is None
