"""Thesis schema, safe formulas, deterministic valuation, and CLI contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from alphasieve.cli.main import main
from alphasieve.thesis import Thesis, evaluate_scenarios, implied, load_thesis
from alphasieve.thesis.formula import FormulaError, evaluate, references

SAMPLE = Path(__file__).resolve().parents[1] / "theses" / "hog-cycle-muyuan.yaml"


def synthetic():
    return {
        "thesis_id": "test-thesis", "title": "Test", "assets": [{"code": "X", "name": "X"}],
        "status": "researching", "as_of": "2026-01-01", "core_variables": ["x"],
        "argument": ["x drives value"],
        "parameters": {
            "x": {"base": 2, "unit": "u", "low": 1, "high": 4, "evidence": ["e"]},
            "y": {"base": 3, "unit": "u", "low": 2, "high": 4, "evidence": ["e"]},
        },
        "evidence": [{"id": "e", "claim": "observed", "value": 2, "source": "test source",
                      "grade": "A", "accessed": "2026-01-01"}],
        "valuation": {"formula": "x * y + 1", "output_unit": "u", "price_param": "x", "market_price": 10},
        "scenarios": {"up": {"x": 4}}, "falsifiers": [], "position_limit": 0,
        "proposed_forecasts": [], "revisions": [],
    }


@pytest.mark.parametrize("expression", [
    "x.__class__", "__import__('os')", "open('x')", "x[0]", "[x for x in (1,)]",
    "lambda: x", "(x if y else 0)", "min.__class__", "min(x, z)", "abs(x, y)",
    "True", "x // y", "{x: y}",
])
def test_reject_unsafe_formulas(expression):
    with pytest.raises(FormulaError):
        references(expression, {"x", "y"})


def test_safe_arithmetic_and_nonfinite():
    assert evaluate("max(2, x) + abs(-y) ** 2 / min(2, 4)", {"x": 3, "y": 4}) == 11
    with pytest.raises(FormulaError):
        evaluate("1 / (x - x)", {"x": 2})
    with pytest.raises(FormulaError):
        evaluate("(-1) ** 0.5", {"x": 2})


@pytest.mark.parametrize("mutate", [
    lambda d: d["parameters"]["x"].update(evidence=["missing"]),
    lambda d: d["scenarios"]["up"].update(z=3),
    lambda d: d["valuation"].update(formula="x + z"),
    lambda d: d["evidence"][0].update(source="  "),
    lambda d: d["evidence"][0].update(grade="D"),
    lambda d: d.update(position_limit=2),
    lambda d: d.update(thesis_id="INVALID"),
])
def test_validation_errors(mutate):
    data = synthetic()
    mutate(data)
    with pytest.raises(ValidationError):
        Thesis.model_validate(data)


def test_scenarios_sensitivity_and_implied():
    thesis = Thesis.model_validate(synthetic())
    result = evaluate_scenarios(thesis)
    assert result["base"] == 7
    assert result["scenarios"]["up"] == 13
    assert [row["parameter"] for row in result["sensitivity"]] == ["x", "y"]
    assert result["sensitivity"][0]["low_impact"] == -3
    assert implied(thesis, "x")["value"] == pytest.approx(3)
    assert implied(thesis, "x", 25)["value"] == pytest.approx(8)  # bounds widened
    no_root_data = synthetic()
    no_root_data["valuation"] = {"formula": "x ** 2 + 5", "output_unit": "u", "market_price": 2}
    with pytest.raises(FormulaError, match="no root"):
        implied(Thesis.model_validate(no_root_data), "x")


def test_sample_headlines_and_evidence():
    thesis = load_thesis(SAMPLE)
    result = evaluate_scenarios(thesis)
    assert result["base"] == pytest.approx(41.99, abs=0.01)
    assert result["scenarios"]["guangfa_2027"] == pytest.approx(41.99, abs=0.01)
    assert result["scenarios"]["original"] == pytest.approx(115.36, abs=0.01)
    assert result["scenarios"]["revised_cost_low_with_expansion"] == pytest.approx(101.91, abs=0.01)
    assert result["scenarios"]["revised_cost_high_with_expansion"] == pytest.approx(86.52, abs=0.01)
    assert implied(thesis, "hog_price")["value"] == pytest.approx(14.039825, abs=1e-5)
    missing = next(item for item in thesis.evidence if item.id == "missing_volume_statement")
    assert missing.grade == "D" and missing.value is None


def test_cli_commands_without_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(tmp_path / "hot"))
    monkeypatch.setenv("ALPHASIEVE_STORE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("ALPHASIEVE_ROLE", "human")
    calls = [
        (["thesis", "validate", str(SAMPLE), "--json"], "valid"),
        (["thesis", "scenarios", str(SAMPLE), "--json"], "scenarios"),
        (["thesis", "implied", str(SAMPLE), "--param", "hog_price", "--json"], "value"),
        (["thesis", "show", str(SAMPLE), "--json"], "parameters"),
        (["thesis", "list", "--json"], "theses"),
    ]
    for argv, key in calls:
        assert main(argv) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "ok" and key in output["data"]
    assert not (tmp_path / "hot").exists()
