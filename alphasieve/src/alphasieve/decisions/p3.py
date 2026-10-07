"""P3 position sizing and rebalance bands, evaluated on synthetic accounts against no action, a calendar
rebalance and the matched-random baseline (docs/personal/personal-decision-tasks §5)."""

import hashlib
import itertools
import json
import os
import uuid
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from alphasieve.config import Settings, load_config
from alphasieve.decisions import ledger
from alphasieve.decisions.bars import dev_end, load_bars
from alphasieve.decisions.simulate import (
    Costs,
    Rule,
    certainty_equivalent,
    gather,
    matched_random,
    simulate,
    trailing_vol,
)
from alphasieve.decisions.synthetic import AccountSpec, draw_accounts
from alphasieve.util import canonical_json, utcnow_iso

TASK = "P3"


def load_task(settings: Settings, overrides: dict | None = None) -> tuple[dict, str]:
    cfg = load_config(settings, "decisions/p3")
    for key, value in (overrides or {}).items():
        section, _, field = key.partition(".")
        if field:
            cfg[section] = {**cfg[section], field: value}
        else:
            cfg[section] = value
    return cfg, hashlib.sha256(canonical_json(cfg).encode("utf-8")).hexdigest()


def grid(cfg: dict) -> list[Rule]:
    g = cfg["grid"]
    return [Rule(t, c, b, r) for t, c, b, r in itertools.product(g["target"], g["cap"], g["band"], g["review_days"])]


def holm(pvalues: list[float]) -> list[float]:
    order = np.argsort(pvalues)
    m, adjusted, running = len(pvalues), [0.0] * len(pvalues), 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def _clustered_t(values: np.ndarray, clusters: np.ndarray) -> float | None:
    means = np.array([values[clusters == c].mean() for c in np.unique(clusters)])
    if len(means) < 3 or means.std(ddof=1) == 0:
        return None
    return float(means.mean() / (means.std(ddof=1) / np.sqrt(len(means))))


def _summary(result: dict, hold: dict, gammas: list[float], years: np.ndarray, months: np.ndarray) -> dict:
    wealth = result["wealth"]
    value = wealth - hold["wealth"]
    cohorts = {}
    for year in np.unique(years):
        sel = years == year
        cohorts[str(year)] = {"accounts": int(sel.sum()),
                              "ce_gamma4": certainty_equivalent(wealth[sel], 4),
                              "hold_ce_gamma4": certainty_equivalent(hold["wealth"][sel], 4),
                              "p_mdd_30": float((result["max_drawdown"][sel] > 0.3).mean())}
    return {
        **{f"ce_gamma{g:g}": certainty_equivalent(wealth, g) for g in gammas},
        "mean_wealth": float(wealth.mean()), "median_wealth": float(np.median(wealth)),
        "p_loss_30": float((wealth < 0.7).mean()),
        "p_mdd_30": float((result["max_drawdown"] > 0.3).mean()),
        "mean_cost": float(result["cost"].mean()),
        "mean_rebalances": float(result["rebalances"].mean()) if "rebalances" in result else 0.0,
        "decision_value": {"mean": float(value.mean()), "share_positive": float((value > 0).mean()),
                           "t_by_start_month": _clustered_t(value, months)},
        "cohorts": cohorts,
    }


def _accept(row: dict, mechanical: dict, acceptance: dict, horizon_years: float) -> dict:
    cohorts = row["cohorts"]
    wins = [c["ce_gamma4"] >= c["hold_ce_gamma4"] for c in cohorts.values()]
    stress = {y: cohorts[y]["ce_gamma4"] - cohorts[y]["hold_ce_gamma4"] for y in acceptance["stress_cohorts"]
              if y in cohorts}
    checks = {
        "cohort_win_share": {"value": float(np.mean(wins)),
                             "pass": float(np.mean(wins)) >= acceptance["cohort_win_share"]},
        "stress_cohorts": {"value": stress, "pass": all(v >= -acceptance["stress_tolerance"] for v in stress.values())},
        "annual_cost": {"value": row["mean_cost"] / horizon_years,
                        "pass": row["mean_cost"] / horizon_years < acceptance["max_annual_cost"]},
        "beats_matched_random": {"value": row["random"]["p_holm"],
                                 "pass": row["random"]["p_holm"] is not None
                                 and row["random"]["p_holm"] < acceptance["random_alpha"]},
        "not_worse_than_mechanical": {"value": row["ce_gamma4"] - mechanical["ce_gamma4"],
                                      "pass": row["ce_gamma4"] >= mechanical["ce_gamma4"]
                                      - acceptance["mechanical_tolerance"]},
    }
    return {"checks": checks, "pass": all(c["pass"] for c in checks.values())}


def evaluate_pool(settings: Settings, cfg: dict, pool: str, pool_index: int) -> dict:
    bars = load_bars(settings)
    vol = trailing_vol(bars)
    spec = AccountSpec(pool=pool, **{**cfg["accounts"], "holdings": tuple(cfg["accounts"]["holdings"])})
    accounts = draw_accounts(bars, spec)
    win = gather(bars, accounts, vol)
    costs = Costs(**cfg["costs"])
    starts = bars.dates[accounts.start].astype(str)
    years, months = np.array([d[:4] for d in starts]), np.array([d[:7] for d in starts])
    gammas = cfg["gammas"]
    hold = simulate(win, None, costs)
    mech_rule = Rule(**cfg["mechanical"])
    mech = simulate(win, mech_rule, costs)
    rows = []
    for index, rule in enumerate(grid(cfg)):
        result = simulate(win, rule, costs)
        rng = np.random.default_rng([cfg["accounts"]["seed"], pool_index, index])
        draws = [simulate(win, rule, costs, forced=matched_random(result["triggered"], rng))
                 for _ in range(cfg["random_reps"])]
        row = {"rule_id": rule.rule_id, "rule": rule.__dict__, **_summary(result, hold, gammas, years, months)}
        random_ce = np.array([certainty_equivalent(d["wealth"], 4) for d in draws])
        always = bool(result["triggered"].all())
        row["random"] = {"ce_gamma4_mean": float(random_ce.mean()),
                         "ce_gamma4_q95": float(np.quantile(random_ce, 0.95)),
                         "p": None if always else float((1 + (random_ce >= row["ce_gamma4"]).sum()) / (1 + len(draws))),
                         "note": "every review rebalanced; timing cannot differ from random" if always else None}
        rows.append(row)
    pvals = [r["random"]["p"] for r in rows]
    valid = [i for i, p in enumerate(pvals) if p is not None]
    for i, adj in zip(valid, holm([pvals[i] for i in valid]), strict=True):
        rows[i]["random"]["p_holm"] = adj
    for r in rows:
        r["random"].setdefault("p_holm", None)
    hold_row = _summary(hold, hold, gammas, years, months)
    mech_row = {"rule_id": mech_rule.rule_id, **_summary(mech, hold, gammas, years, months)}
    best = max(rows, key=lambda r: r[cfg["selection"]])
    horizon_years = cfg["accounts"]["horizon_days"] / 244
    for r in rows:
        r["acceptance"] = _accept(r, mech_row, cfg["acceptance"], horizon_years)
    k = accounts.mask.sum(axis=1)
    return {"pool": pool, "accounts": int(len(accounts.start)),
            "start_range": [min(starts.tolist()), max(starts.tolist())],
            "holdings_mix": {str(n): int((k == n).sum()) for n in sorted(set(k.tolist()))},
            "no_action": hold_row, "mechanical": mech_row, "rules": rows, "selected": best["rule_id"],
            "selected_acceptance": best["acceptance"]}


def run(settings: Settings, conn, actor: str, overrides: dict | None = None, record: bool = True,
        workers: int = 3) -> dict:
    cfg, config_hash = load_task(settings, overrides)
    n_trials = len(grid(cfg)) * len(cfg["pools"])
    if record:
        ledger.check_budget(conn, TASK, cfg["trial_budget"], n_trials)
    load_bars(settings)  # build the shared cache once before the workers start
    with ProcessPoolExecutor(min(workers, len(cfg["pools"]))) as pool:
        futures = [pool.submit(evaluate_pool, settings, cfg, p, i) for i, p in enumerate(cfg["pools"])]
        pools = [f.result() for f in futures]
    run_id = uuid.uuid4().hex
    report = {"task": TASK, "run_id": run_id, "tier": "dev", "data_end": dev_end(settings),
              "config_hash": config_hash, "config": cfg, "pools": pools, "recorded": record,
              "created_at": utcnow_iso(),
              "method": "Synthetic accounts hold 2-5 names with Dirichlet weights from a point-in-time pool; each "
                        "rule "
                        "is simulated for one year with T+1 open execution, whole lots, limit and suspension blocks "
                        "and costs. Matched random keeps each account's rebalance count and target but draws the "
                        "review days at random; its p-value is one-sided on the gamma-4 certainty equivalent and "
                        "Holm-adjusted across the rules of a pool. Delisted names are carried at their last close "
                        "(optimistic)."}
    if record:
        for p in pools:
            for r in p["rules"]:
                metrics = {k: r[k] for k in ("ce_gamma2", "ce_gamma4", "p_mdd_30", "mean_cost", "mean_rebalances")}
                metrics["p_holm"] = r["random"]["p_holm"]
                ledger.record(conn, TASK, run_id, r["rule_id"], p["pool"], config_hash, metrics, actor)
    path = settings.hot_root / "reports" / "decisions" / f"p3-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)
    if record:
        latest = path.with_name("p3-latest.json")
        tmp.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        os.replace(tmp, latest)
    report["report_path"] = str(path)
    return report
