import math


def _check(name: str, value, threshold, op: str) -> dict:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        passed = False
    elif op == ">=":
        passed = value >= threshold
    elif op == ">":
        passed = value > threshold
    elif op == "<=":
        passed = value <= threshold
    else:
        raise ValueError(op)
    return {"name": name, "value": value, "threshold": threshold, "op": op, "passed": bool(passed)}


def _result(checks: list[dict], **extra) -> dict:
    return {"passed": all(c["passed"] for c in checks), "checks": checks, **extra}


def gate_l0(issues: list) -> dict:
    checks = [{"name": i.name, "value": i.message, "threshold": None, "op": "==", "passed": False} for i in issues]
    if not checks:
        checks = [{"name": "structure", "value": "ok", "threshold": None, "op": "==", "passed": True}]
    return _result(checks)


def gate_l1(m: dict, policy: dict, library_icir: dict[str, float]) -> dict:
    p = policy["l1"]
    checks = [
        _check("coverage", m["coverage"], p["min_coverage"], ">="),
        _check("valid_dates", m["valid_dates"], p["min_valid_dates"], ">="),
        _check("ic_mean", m["ic_mean"], p["min_ic_mean"], ">="),
        _check("icir", m["icir"], p["min_icir"], ">="),
    ]
    corr = m["library"]["max_abs_corr"]
    corr_check = _check("library_corr", corr, p["max_library_corr"], "<=")
    replacement = False
    if not corr_check["passed"] and m["library"]["max_corr_with"] in library_icir:
        other = library_icir[m["library"]["max_corr_with"]]
        if other and not math.isnan(other) and m["icir"] >= p["replacement_icir_ratio"] * abs(other):
            corr_check["passed"] = True
            replacement = True
    checks.append(corr_check)
    return _result(checks, replacement_candidate=replacement)


def gate_l2(m: dict, policy: dict, neighborhood_trials: int, params_source: str) -> dict:
    p = policy["l2"]
    same_sign = sum(1 for v in m["subwindow_ic"] if v > 0)
    neutral_ratio = m["neutral"]["ic_mean"] / m["ic_mean"] if m["ic_mean"] else float("nan")
    checks = [
        _check("subwindows_same_sign", same_sign, p["min_subwindows_same_sign"], ">="),
        _check("neutral_ratio", neutral_ratio, p["min_neutral_ratio"], ">="),
        _check("cost_adjusted_excess", m["tradable"].get("annual_excess_net"), p["min_cost_adjusted_excess"], ">"),
        _check("marginal_ic", m["marginal"]["marginal_ic"], p["min_marginal_ic"], ">"),
    ]
    if params_source == "neighborhood":
        checks.append(_check("neighborhood_trials", neighborhood_trials, p["max_neighborhood_trials"], "<="))
    return _result(checks)
