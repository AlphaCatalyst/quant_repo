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
            passed = f"{c['passed']} (recorded only)" if c.get("informational") else c["passed"]
            lines.append(f"| {level} | {c['name']} | {value} | {c['threshold']} | {passed} |")
    if result["cell"]["warnings"]:
        lines += ["", "Cell warnings:", *[f"- {w}" for w in result["cell"]["warnings"]]]
    return "\n".join(lines) + "\n"


def _reject_duplicate(conn: sqlite3.Connection, campaign_id: str, candidate_hash: str) -> None:
    kinds = {r["trial_id"]: set() for r in conn.execute(
        "SELECT trial_id FROM trials WHERE campaign_id = ? AND candidate_hash = ? AND evidence_tier = 'dev'",
        (campaign_id, candidate_hash))}
    for r in conn.execute("SELECT trial_id, record_kind, outcome FROM trials WHERE campaign_id = ?"
                          " AND candidate_hash = ?", (campaign_id, candidate_hash)):
        if r["outcome"] == "validation_failed":
            kinds.pop(r["trial_id"], None)
        elif r["trial_id"] in kinds:
            kinds[r["trial_id"]].add(r["record_kind"])
    if any("completed" in k or k == {"started"} for k in kinds.values()):
        raise AlphaSieveError("CONFLICT", "this candidate is already evaluated (or being evaluated) in this campaign;"
                              " results are deterministic, read them with `alphasieve factor show`",
                              {"candidate_hash": candidate_hash})


def build_job(settings: Settings, conn: sqlite3.Connection, spec: FactorSpec, canonical: str, candidate_hash: str,
              tier: str, policy: dict, costs: dict) -> dict:
    """Everything a worker needs to compute an evaluation without access to the state database."""
    members = [{"name": m["name"] or m["factor_id"], "canonical": m["canonical_expression"],
                "candidate_hash": m["candidate_hash"], "direction": m["direction"]} for m in lib.library_members(conn)]
    return {"job_id": uuid.uuid4().hex[:16], "tier": tier, "spec": spec.model_dump(), "canonical": canonical,
            "candidate_hash": candidate_hash, "policy": policy, "costs": costs, "library": members,
            "library_icir": lib.library_icir(conn),
            "neighborhood_trials": neighborhood_count(conn, spec.neighborhood_of)}


def compute_job(settings: Settings, job: dict, panel=None) -> dict:
    """Pure computation of L1/L2 for a structurally valid candidate. Runs locally or in an evaluation worker."""
    if job["tier"] != "dev":
        raise AlphaSieveError("PERMISSION_DENIED", "evaluation workers only compute dev-tier jobs")
    spec = FactorSpec(**job["spec"])
    policy, costs = job["policy"], job["costs"]
    space = load_search_space(settings)
    compiled = compile_expression(job["canonical"], space)
    if panel is None or getattr(panel, "meta", {}).get("universe", "csi800") != spec.universe:
        panel = load_panel(settings, job["tier"], role="system", universe=spec.universe)
    start, end = panel.window
    out = {"cell": space.check_cell(spec.cell.model_dump(), compiled.terminals, compiled.max_window),
           "window": f"{start.date()}..{end.date()}", "panel_signature": panel.signature, "gates": {},
           "code_version": code_version()}
    inputs = EvalInputs(panel, spec.horizon)
    raw = evaluate(compiled, panel)
    factor = raw * spec.direction
    library = lib.frames_from_members(settings, panel, job["library"])
    out["library"] = sorted(library)
    metrics = l1_metrics(factor, inputs, library)
    gates = out["gates"]
    gates["l1"] = gate_l1(metrics, policy, job["library_icir"])
    path = ["evaluating"]
    if not gates["l1"]["passed"]:
        outcome = "evaluation_failed"
        path.append(outcome)
    else:
        path += ["evaluated", "robust_evaluating"]
        baseline = library or lib.base_feature_frames(settings, panel)
        metrics["l2"] = l2_metrics(factor, inputs, baseline, costs, policy["l2"]["subwindows"], metrics["_ic_series"])
        gates["l2"] = gate_l2({**metrics["l2"], "ic_mean": metrics["ic_mean"]}, policy, job["neighborhood_trials"],
                              spec.params_source)
        outcome = "robust_passed" if gates["l2"]["passed"] else "robust_failed"
        path.append(outcome)
        if outcome == "robust_passed":
            lib.store_values(settings, panel, job["candidate_hash"], raw)
    out.update(outcome=outcome, path=path, metrics=public_metrics(metrics))
    return out


def evaluate_spec(settings: Settings, conn: sqlite3.Connection, spec: FactorSpec, campaign_id: str | None = None,
                  tier: str = "dev", executor=None) -> dict:
    """The single evaluation entry point: validate and record locally, compute via ``executor`` (default:
    in-process), then write the artifact, the ledger result and the state transition locally."""
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
    if campaign is not None and spec.universe != campaign.universe:
        message = f"spec universe {spec.universe} differs from the campaign universe {campaign.universe}"
        issues = [*issues, DSLIssue("campaign_universe", message)]
    if campaign is not None and spec.horizon != campaign.horizon:
        message = f"spec horizon {spec.horizon} differs from the campaign horizon {campaign.horizon}"
        issues = [*issues, DSLIssue("campaign_horizon", message)]
    if compiled is not None:
        outside = domain_violations(campaign, compiled.terminals, settings)
        if outside:
            issues = [*issues, DSLIssue("campaign_domain",
                               f"terminals {outside} are outside campaign domains {campaign.domains}")]
    canonical = compiled.canonical if compiled else spec.expression.strip()
    candidate_hash = compiled.candidate_hash if compiled else sha256_hex("invalid:" + canonical)[:16]
    if role == "agent" and campaign_id:
        _reject_duplicate(conn, campaign_id, candidate_hash)
    factor_id, version, created = register(conn, spec, canonical, candidate_hash, role)
    base = dict(trial_id=trial_id, campaign_id=campaign_id, factor_id=factor_id, version=version,
                candidate_hash=candidate_hash, evidence_tier=tier, gate_policy_version=policy["version"],
                search_space_version=space.version_tag, created_by=role, turn_id=settings.turn)
    append_trial(conn, TrialLedgerEntry(record_kind="started", **base))
    result = {"trial_id": trial_id, "factor_id": factor_id, "version": version, "candidate_hash": candidate_hash,
              "canonical": canonical, "evidence_tier": tier, "gates": {}, "window": None,
              "cell": {"warnings": []}, "new_candidate": created}
    try:
        gates = {"l0": gate_l0(issues)}
        path: list[str] = ["validating"]
        public: dict = {}
        if not gates["l0"]["passed"]:
            outcome = "validation_failed"
            path.append(outcome)
        else:
            path.append("validated")
            job = build_job(settings, conn, spec, canonical, candidate_hash, tier, policy, costs)
            computed = executor(job) if executor is not None else compute_job(settings, job)
            if computed.get("error"):
                raise AlphaSieveError("INTERNAL", f"evaluation worker failed: {computed['error']}")
            gates.update(computed["gates"])
            path += computed["path"]
            outcome, public = computed["outcome"], computed["metrics"]
            result.update(cell=computed["cell"], window=computed["window"], library=computed["library"],
                          panel_signature=computed["panel_signature"])
            if computed.get("worker"):
                result["worker"] = computed["worker"]
        result["gates"] = gates
        result["outcome"] = outcome
        artifact_id = None
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
