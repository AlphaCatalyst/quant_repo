"""Gate calibration: null simulation with random expressions, planted-signal detection and
seed-based threshold suggestions. Calibration does not select factors, so it does not write trials."""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.evaluation.core import EvalInputs, l1_metrics
from alphasieve.factors.dsl import DSLError, EvalContext, compile_expression, evaluate
from alphasieve.gates import gate_l1
from alphasieve.search_space import SearchSpace

TS_OPS = ["ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ts_rank", "ts_delta", "ts_zscore"]
UNARY = ["cs_rank", "abs", "log", "sign", "neg"]
BINARY = ["add", "sub", "mul", "div"]


def random_expression(rng: np.random.Generator, space: SearchSpace, terminals: list[str], depth: int = 2) -> str:
    def leaf() -> str:
        op = rng.choice(TS_OPS)
        return f"{op}({rng.choice(terminals)}, {int(rng.choice(space.windows))})"

    def build(d: int) -> str:
        if d == 0:
            return leaf()
        roll = rng.random()
        if roll < 0.4:
            return f"{rng.choice(BINARY)}({build(d - 1)}, {build(d - 1)})"
        if roll < 0.7:
            return f"{rng.choice(UNARY)}({build(d - 1)})"
        return leaf()

    return build(depth)


def null_simulation(panel: Panel, space: SearchSpace, policy: dict, library: dict[str, pd.DataFrame],
                    library_icir: dict[str, float], n: int = 200, seed: int = 0, horizon: int = 5) -> dict:
    rng = np.random.default_rng(seed)
    terminals = [t for t in sorted(space.terminals) if panel.has(t) or t in
                 ("excess_ret_1d", "amihud_20d", "vwap_dev", "overnight_ret", "intraday_ret")]
    inputs = EvalInputs(panel, horizon)
    rows = []
    attempts = 0
    while len(rows) < n and attempts < n * 5:
        attempts += 1
        expr = random_expression(rng, space, terminals, depth=int(rng.integers(0, 3)))
        try:
            compiled = compile_expression(expr, space)
        except DSLError:
            continue
        direction = int(rng.choice([-1, 1]))
        factor = evaluate(compiled, panel, EvalContext(panel)) * direction
        m = l1_metrics(factor, inputs, library)
        gate = gate_l1(m, policy, library_icir)
        failed = [c["name"] for c in gate["checks"] if not c["passed"]]
        rows.append({"expression": compiled.canonical, "direction": direction, "ic_mean": m["ic_mean"],
                     "icir": m["icir"], "coverage": m["coverage"], "l1_passed": gate["passed"], "failed": failed})
    df = pd.DataFrame(rows)
    return {
        "n": int(len(df)),
        "l1_pass_rate": float(df["l1_passed"].mean()) if len(df) else float("nan"),
        "icir_quantiles": {str(q): float(df["icir"].quantile(q)) for q in (0.5, 0.9, 0.95, 0.99)} if len(df) else {},
        "failure_counts": pd.Series([f for fs in df["failed"] for f in fs]).value_counts().to_dict() if len(df) else {},
        "passed_examples": df.loc[df["l1_passed"], ["expression", "direction", "icir"]].head(10).to_dict("records"),
    }


def planted_detection(panel: Panel, policy: dict, target_ics=(0.01, 0.02, 0.03, 0.05), seed: int = 0,
                      horizon: int = 5, trials: int = 5) -> dict:
    """Blend the (future) label with noise to create signals of known strength and check L1 detection."""
    rng = np.random.default_rng(seed)
    inputs = EvalInputs(panel, horizon)
    label_rank = inputs.label.where(inputs.valid).rank(axis=1, pct=True) - 0.5
    out = {}
    for target in target_ics:
        detected, realised = 0, []
        for _ in range(trials):
            noise = pd.DataFrame(rng.uniform(-0.5, 0.5, label_rank.shape), index=label_rank.index,
                                 columns=label_rank.columns)
            weight = target / np.sqrt(1 - target**2)
            factor = (weight * label_rank.fillna(0) + noise).where(inputs.universe)
            m = l1_metrics(factor, inputs, {})
            realised.append(m["ic_mean"])
            detected += gate_l1(m, policy, {})["passed"]
        out[str(target)] = {"detection_rate": detected / trials, "realised_ic_mean": float(np.mean(realised))}
    return out


def seed_suggestions(members: list[dict]) -> dict:
    icirs = [m["metrics"].get("icir") for m in members if m["metrics"].get("icir") is not None]
    ics = [m["metrics"].get("ic_mean") for m in members if m["metrics"].get("ic_mean") is not None]
    positive = [v for v in icirs if v > 0]
    return {
        "seed_count": len(members),
        "seed_icir_quantiles": {str(q): float(np.quantile(icirs, q)) for q in (0.25, 0.5, 0.75)} if icirs else {},
        "seed_ic_quantiles": {str(q): float(np.quantile(ics, q)) for q in (0.25, 0.5, 0.75)} if ics else {},
        "suggested_min_icir": float(np.quantile(positive, 0.5)) if positive else None,
        "note": "suggestion only; gate_policy.yaml changes require human review",
    }
