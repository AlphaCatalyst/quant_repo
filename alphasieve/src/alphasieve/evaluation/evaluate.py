"""The single evaluation entry point. Every call is recorded in the trial ledger."""

import sqlite3
import uuid

from alphasieve.artifacts import write_artifact
from alphasieve.campaigns.service import domain_violations, ensure_can_evaluate
from alphasieve.config import Settings, load_config
from alphasieve.contracts import FactorSpec, TrialLedgerEntry
from alphasieve.data.access import check_tier_access, load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation.core import EvalInputs, l1_metrics, l2_metrics, public_metrics
from alphasieve.factors import library as lib
from alphasieve.factors.derived import DERIVED_VERSION
from alphasieve.factors.dsl import DSLError, DSLIssue, compile_expression, evaluate
from alphasieve.factors.registry import neighborhood_count, register
from alphasieve.gates import advance, gate_l0, gate_l1, gate_l2
from alphasieve.ledger import append_trial
from alphasieve.search_space import load_search_space
from alphasieve.util import code_version, sha256_hex

METRICS_SCHEMA = 1


def _summary(metrics: dict) -> dict:
    keys = ("ic_mean", "icir", "ic_positive_ratio", "coverage", "valid_dates", "turnover_proxy", "ic_skew",
            "ic_kurtosis")
    out = {k: metrics.get(k) for k in keys if k in metrics}
    if "library" in metrics:
        out["library_max_abs_corr"] = metrics["library"]["max_abs_corr"]
        out["library_max_corr_with"] = metrics["library"]["max_corr_with"]
    if "quantiles" in metrics:
        out["long_short"] = metrics["quantiles"]["long_short"]
    l2 = metrics.get("l2")
    if l2:
        out["subwindow_ic"] = l2["subwindow_ic"]
        out["neutral_ic_mean"] = l2["neutral"]["ic_mean"]
        out["annual_excess_net"] = l2["tradable"].get("annual_excess_net")
        out["marginal_ic"] = l2["marginal"]["marginal_ic"]
    return out


def _report(spec: FactorSpec, result: dict) -> str:
    lines = [f"# Factor evaluation: {spec.name}", "", f"- expression: `{result['canonical']}`",
             f"- candidate: {result['candidate_hash']}  factor: {result['factor_id']}@{result['version']}",
             f"- evidence tier: {result['evidence_tier']}  window: {result['window']}",
             f"- outcome: **{result['outcome']}**", "", "| gate | check | value | threshold | passed |",
             "|---|---|---|---|---|"]
    for level, gate in result["gates"].items():
        for c in gate["checks"]:
            value = f"{c['value']:.4f}" if isinstance(c["value"], float) else c["value"]
            lines.append(f"| {level} | {c['name']} | {value} | {c['threshold']} | {c['passed']} |")
    if result["cell"]["warnings"]:
        lines += ["", "Cell warnings:", *[f"- {w}" for w in result["cell"]["warnings"]]]
    return "\n".join(lines) + "\n"


def evaluate_spec(settings: Settings, conn: sqlite3.Connection, spec: FactorSpec, campaign_id: str | None = None,
                  tier: str = "dev") -> dict:
    role = settings.role
    check_tier_access(role, tier)
    campaign = ensure_can_evaluate(conn, settings, campaign_id) if tier == "dev" else None
    policy = load_config(settings, "gate_policy")
    costs = load_config(settings, "costs")
    space = load_search_space(settings)
    trial_id = uuid.uuid4().hex[:16]
    issues, compiled = [], None
    try:
        compiled = compile_expression(spec.expression, space)
    except DSLError as exc:
        issues = exc.issues
    if compiled is not None:
        outside = domain_violations(campaign, compiled.terminals, settings)
        if outside:
            issues = [DSLIssue("campaign_domain",
                               f"terminals {outside} are outside campaign domains {campaign.domains}")]
    canonical = compiled.canonical if compiled else spec.expression.strip()
    candidate_hash = compiled.candidate_hash if compiled else sha256_hex("invalid:" + canonical)[:16]
    factor_id, version, created = register(conn, spec, canonical, candidate_hash, role)
    base = dict(trial_id=trial_id, campaign_id=campaign_id, factor_id=factor_id, version=version,
                candidate_hash=candidate_hash, evidence_tier=tier, gate_policy_version=policy["version"],
                search_space_version=space.version_tag, created_by=role)
    append_trial(conn, TrialLedgerEntry(record_kind="started", **base))
    result = {"trial_id": trial_id, "factor_id": factor_id, "version": version, "candidate_hash": candidate_hash,
              "canonical": canonical, "evidence_tier": tier, "gates": {}, "window": None,
              "cell": {"warnings": []}, "new_candidate": created}
    try:
        gates = {"l0": gate_l0(issues)}
        result["gates"] = gates
        path: list[str] = ["validating"]
        metrics: dict = {}
        if not gates["l0"]["passed"]:
            outcome = "validation_failed"
            path.append(outcome)
        else:
            path.append("validated")
            result["cell"] = space.check_cell(spec.cell.model_dump(), compiled.terminals, compiled.max_window)
            panel = load_panel(settings, tier)
            start, end = panel.window
            result["window"] = f"{start.date()}..{end.date()}"
            inputs = EvalInputs(panel, spec.horizon)
            raw = evaluate(compiled, panel)
            factor = raw * spec.direction
            library = lib.library_frames(settings, conn, panel)
            result["library"] = sorted(library)
            metrics = l1_metrics(factor, inputs, library)
            gates["l1"] = gate_l1(metrics, policy, lib.library_icir(conn))
            path += ["evaluating"]
            if not gates["l1"]["passed"]:
                outcome = "evaluation_failed"
                path.append(outcome)
            else:
                path += ["evaluated", "robust_evaluating"]
                baseline = library or lib.baseline_frames(settings, conn, panel)
                metrics["l2"] = l2_metrics(factor, inputs, baseline, costs, policy["l2"]["subwindows"],
                                           metrics["_ic_series"])
                gates["l2"] = gate_l2({**metrics["l2"], "ic_mean": metrics["ic_mean"]}, policy,
                                      neighborhood_count(conn, spec.neighborhood_of), spec.params_source)
                outcome = "robust_passed" if gates["l2"]["passed"] else "robust_failed"
                path.append(outcome)
                if outcome == "robust_passed":
                    lib.store_values(settings, panel, candidate_hash, raw)
            result["panel_signature"] = panel.signature
        result["outcome"] = outcome
        artifact_id = None
        public = public_metrics(metrics)
        if compiled is not None:
            manifest = {
                "kind": "factor_eval", "metrics_schema": METRICS_SCHEMA, "candidate_hash": candidate_hash,
                "canonical": canonical, "spec": spec.model_dump(), "evidence_tier": tier,
                "panel_signature": result.get("panel_signature"), "window": result["window"],
                "gate_policy_version": policy["version"], "costs_version": costs["version"],
                "search_space": space.version_tag, "derived_version": DERIVED_VERSION, "code_version": code_version(),
                "library": result.get("library", []),
            }
            artifact_id = write_artifact(settings, manifest, metrics={"summary": _summary(public), "full": public,
                                                                      "gates": gates, "cell": result["cell"]},
                                         report=_report(spec, result))
        result["artifact_id"] = artifact_id
        result["metrics"] = _summary(public)
        append_trial(conn, TrialLedgerEntry(record_kind="completed", metrics=result["metrics"], gate_results=gates,
                                            outcome=outcome, artifact_id=artifact_id, data_window=result["window"],
                                            **base))
        state = conn.execute("SELECT state FROM factor_specs WHERE factor_id = ? AND version = ?",
                             (factor_id, version)).fetchone()["state"]
        if state == "draft":
            advance(conn, settings, factor_id, version, path, trial_id)
        return result
    except Exception as exc:
        append_trial(conn, TrialLedgerEntry(record_kind="failed", outcome="error",
                                            metrics={"error": f"{type(exc).__name__}: {exc}"}, **base))
        if isinstance(exc, AlphaSieveError):
            raise
        raise AlphaSieveError("INTERNAL", f"evaluation failed: {type(exc).__name__}: {exc}",
                              {"trial_id": trial_id}) from exc