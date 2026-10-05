"""Report-only rm1 CLI commands. No strategy execution or research trial is started."""

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import AlphaSieveError, validation_error

ALL = ("agent", "human", "system")
REMOTE_ROOT = Path(os.environ.get("ALPHASIEVE_REMOTE_ROOT", "/taijifs_zw35/r2/felixjjiang/alphasieve"))


def _report_args(p):
    p.add_argument("--trial", required=True)
    p.add_argument("--model", default="rm1")
    p.add_argument("--tier", default="dev")


@command(
    "risk report",
    ALL,
    configure=_report_args,
    needs_store=True,
    help="build an independent dev risk report for saved trial outputs",
)
def cmd_risk_report(args, ctx) -> CommandResult:
    from alphasieve.training.risk_report import build_report, write_risk_artifact

    if args.tier != "dev":
        raise validation_error("risk report only accepts --tier dev")
    manifest, tables, validation = build_report(ctx.settings, args.trial, tier=args.tier, model=args.model)
    aid = write_risk_artifact(ctx.settings, manifest, tables, validation)
    return CommandResult(
        data={
            "risk_artifact_id": aid,
            "parent_trial_id": args.trial,
            "validation": validation,
            "industry_source": "sw1_pit",
            "risk_spec_hash": manifest["risk_spec_hash"],
        },
        artifacts=[{"artifact_id": aid, "kind": "risk_report"}],
        warnings=validation["warnings"],
    )


def _submit_args(p):
    _report_args(p)
    p.add_argument("--cluster", help="Ray jobs address")
    p.add_argument("--output", type=Path, help="local result.json destination")


def _copy_result(job_id: str, output: Path) -> dict:
    if not re.fullmatch(r"alphasieve-risk-[A-Za-z0-9_-]+", job_id):
        raise validation_error("invalid risk job id")
    source = REMOTE_ROOT / "runs" / job_id / "result.json"
    if not source.is_file():
        raise validation_error(f"Ray result is not ready: {source}")
    raw = source.read_text(encoding="utf-8")
    envelope = json.loads(raw[raw.find("{"):])
    if envelope.get("status") != "ok" or envelope.get("command") != "risk report":
        raise AlphaSieveError("INTERNAL", "Ray risk report failed", details={"result": str(source)})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(raw, encoding="utf-8")
    return envelope


@command("risk submit", ALL, configure=_submit_args, needs_store=True,
         help="submit a dev-only risk report to Ray; no strategy trial is recorded")
def cmd_risk_submit(args, ctx) -> CommandResult:
    import pandas as pd

    from alphasieve.training.risk_report import DEV_END, _check_dev

    if not re.fullmatch(r"S-[A-Za-z0-9_-]+", args.trial):
        raise validation_error("invalid trial id for Ray submission")
    if args.tier != "dev":
        raise validation_error("risk submit only accepts --tier dev")
    matches = []
    for path in ctx.settings.artifacts_dir.glob("*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("trial_id") == args.trial:
            _check_dev(manifest, tier=args.tier)
            matches.append(path)
    if len(matches) != 1:
        raise validation_error("expected exactly one dev parent artifact", trial=args.trial, matches=len(matches))
    panel_meta = json.loads((ctx.settings.panel_dir("dev") / "meta.json").read_text(encoding="utf-8"))
    if panel_meta.get("tier") != "dev" or pd.Timestamp(panel_meta["window"]["end"]) > DEV_END:
        raise validation_error("risk submit refuses a panel after 2022-12-31")
    if panel_meta["signature"] != json.loads(matches[0].read_text())["panel_signature"]:
        raise validation_error("dev panel signature differs from parent")
    source = ctx.settings.raw_dir / "swsresearch" / "sw_industry_hist.parquet"
    history = pd.read_parquet(source)
    history = history.loc[(pd.to_datetime(history["effective_date"]) <= DEV_END)
                          & (pd.to_datetime(history["updated_at"]) <= DEV_END)]
    remote_stage = REMOTE_ROOT / "runs" / "staging"
    remote_stage.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        safe_history = tmp / "sw_industry_hist.parquet"
        history.to_parquet(safe_history, index=False)
        archive = tmp / "risk-inputs.tar"
        with tarfile.open(archive, "w") as tar:
            tar.add(safe_history, arcname="hot/data/raw/swsresearch/sw_industry_hist.parquet")
            for filename in ("manifest.json", "metrics.json"):
                tar.add(matches[0].parent / filename,
                        arcname=f"store/artifacts/{matches[0].parent.name}/{filename}")
        staged = remote_stage / f"risk-{args.trial}-{os.getpid()}.tar"
        shutil.copyfile(archive, staged)
    script = Path(__file__).resolve().parents[3] / "deploy" / "ray" / "submit.sh"
    env = {**os.environ, "ALPHASIEVE_PRE": f"tar -xf {staged} -C {REMOTE_ROOT}"}
    if args.cluster:
        env["RAY_ADDRESS"] = args.cluster
    proc = subprocess.run([str(script), f"risk-{args.trial}", "risk", "report", "--trial", args.trial,
                           "--model", args.model, "--tier", args.tier], capture_output=True, text=True, env=env)
    match = re.search(r"job (alphasieve-risk-\S+);", proc.stdout)
    if proc.returncode != 0 or not match:
        raise AlphaSieveError("INTERNAL", "Ray risk submission failed",
                              details={"stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]})
    job_id = match.group(1)
    output = args.output or ctx.settings.hot_root / "reports" / "risk" / f"{args.trial}.json"
    data = {"job_id": job_id, "result": str(REMOTE_ROOT / "runs" / job_id / "result.json"),
            "local_output": str(output)}
    if not env.get("ALPHASIEVE_NO_WAIT"):
        data["report"] = _copy_result(job_id, output)["data"]
    return CommandResult(data=data)


def _collect_args(p):
    p.add_argument("--job-id", required=True)
    p.add_argument("--output", type=Path, required=True)


@command("risk collect", ALL, configure=_collect_args,
         help="copy a completed Ray risk result.json to a local path")
def cmd_risk_collect(args, ctx) -> CommandResult:
    envelope = _copy_result(args.job_id, args.output)
    return CommandResult(data={"output": str(args.output), "report": envelope["data"]})


def _validate_args(p):
    p.add_argument("--risk-artifact", required=True)


@command(
    "risk validate",
    ALL,
    configure=_validate_args,
    needs_store=True,
    help="verify a saved risk artifact and its fixed bias evidence",
)
def cmd_risk_validate(args, ctx) -> CommandResult:
    from alphasieve.training.risk_report import read_risk_artifact

    manifest, validation = read_risk_artifact(ctx.settings, args.risk_artifact)
    return CommandResult(
        data={
            "risk_artifact_id": args.risk_artifact,
            "model": manifest["model"],
            "verdict": validation["status"],
            "validation": validation,
        },
        warnings=validation.get("warnings", []),
    )


def _show_args(p):
    p.add_argument("--artifact", required=True)


@command("risk show", ALL, configure=_show_args, needs_store=True, help="show a verified risk artifact summary")
def cmd_risk_show(args, ctx) -> CommandResult:
    from alphasieve.training.risk_report import read_risk_artifact

    manifest, validation = read_risk_artifact(ctx.settings, args.artifact)
    return CommandResult(
        data={"risk_artifact_id": args.artifact, "manifest": manifest, "validation": validation},
        warnings=validation.get("warnings", []),
    )
