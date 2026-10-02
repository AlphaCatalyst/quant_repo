#!/usr/bin/env python3
"""Create an isolated, clearly synthetic dashboard fixture under /tmp.

Run `uv run python tools/web_fixture.py`, then use the printed serve command.
This script never accesses the configured production hot/store roots or panels.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alphasieve.config import PACKAGE_CONFIG_DIR, Settings
from alphasieve.contracts import Campaign, FactorSpec
from alphasieve.contracts.models import TrialLedgerEntry
from alphasieve.ledger import append_trial, verify_ledger
from alphasieve.state import connect


ROOT = Path("/tmp/alphasieve-web-inbox-synthetic")
CAMPAIGN = "c-synthetic-web"
APPROVED = "S-000000000001"
HOLDOUT = APPROVED + "-H"
PENDING = "S-000000000002"
TASK = "synthetic_web_demo"
CHECKS = {
    "annual_excess": [0.062, 0.04, True],
    "information_ratio": [1.26, 0.8, True],
    "max_drawdown_excess": [-0.077, -0.12, True],
    "positive_years": ["4/4", 0.5, True],
    "capacity_drop_at_aum": [0.09, 0.15, True],
}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def strategy(conn, trial_id: str, tier: str, artifact_id: str, ir: float) -> None:
    variant = "v2" if trial_id == PENDING else "v1"
    common = {"trial_id": trial_id, "candidate_hash": f"synthetic-locked-config-{variant}",
              "evidence_tier": tier, "layer": "strategy", "scope": "A", "created_by": "human"}
    append_trial(conn, TrialLedgerEntry(**common, record_kind="started", metrics={"task_id": TASK}))
    append_trial(conn, TrialLedgerEntry(
        **common, record_kind="completed", artifact_id=artifact_id,
        outcome="holdout_passed" if tier == "holdout" else "dev_passed",
        data_window="合成演示区间 2024-01—2024-12",
        metrics={"annual_excess": 0.062, "information_ratio": ir,
                 "search_discount": {"trials": 2, "null_expected_max_ratio": 0.4,
                                     "deflated_ratio": ir - 0.4}}))


def main() -> None:
    if ROOT.exists():
        raise SystemExit(f"已存在 {ROOT}；为避免覆盖数据，请先自行选择新的空目录")
    ROOT.mkdir(mode=0o700)
    hot = ROOT / "hot"
    store = ROOT / "store"
    settings = Settings(hot_root=hot, store_root=store, store_mount=None,
                        config_dir=PACKAGE_CONFIG_DIR, role="human", user="synthetic-fixture")
    conn = connect(settings.state_db)

    spec = Campaign(campaign_id=CAMPAIGN, title="【合成演示】因子研究",
                    question="合成数据，用于看板截图；不代表真实投资结论。",
                    domains=["price"], cells=[{"domain": "price", "form": "reversal", "scale": "short"}],
                    agents=[{"harness": "fake", "model": "synthetic"}])
    conn.execute("INSERT INTO campaigns (campaign_id, title, spec_json, status, created_by, created_at,"
                 " started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (CAMPAIGN, spec.title, spec.model_dump_json(), "running", "synthetic-fixture",
                  "2026-09-01T00:00:00Z", (datetime.now(UTC) - timedelta(hours=2)).isoformat()))
    factor = FactorSpec(name="synthetic_reversal", expression="ts_sum(excess_ret_1d, 3)",
                        hypothesis="合成演示因子", cell={"domain": "price", "form": "reversal", "scale": "short"},
                        direction=-1)
    conn.execute("INSERT INTO factor_specs (factor_id, version, candidate_hash, name, spec_json,"
                 " canonical_expression, state, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("F-000001", 1, "synthetic-factor-hash", factor.name, factor.model_dump_json(),
                  factor.expression, "draft", "synthetic-fixture", "2026-09-01T00:00:00Z"))
    append_trial(conn, TrialLedgerEntry(trial_id="T-synthetic-factor-1", record_kind="started",
                                        campaign_id=CAMPAIGN, factor_id="F-000001", version=1,
                                        candidate_hash="synthetic-factor-hash", created_by="agent"))
    append_trial(conn, TrialLedgerEntry(trial_id="T-synthetic-factor-1", record_kind="completed",
                                        campaign_id=CAMPAIGN, factor_id="F-000001", version=1,
                                        candidate_hash="synthetic-factor-hash", created_by="agent",
                                        metrics={"ic_mean": 0.049, "icir": 0.74}, outcome="robust_passed"))

    for trial_id, tier, artifact_id, ir in ((APPROVED, "dev", "a" * 24, 1.34),
                                             (HOLDOUT, "holdout", "b" * 24, 1.26),
                                             (PENDING, "dev", "c" * 24, 1.05)):
        strategy(conn, trial_id, tier, artifact_id, ir)
        write_json(settings.artifacts_dir / artifact_id / "metrics.json", {
            "synthetic_fixture": True,
            "acceptance": {"passed": True, "judged_on": "合成演示数据", "checks": CHECKS},
            "bundle": {"task": {"task_id": TASK, "description": "【合成演示】策略配置",
                               "synthetic_variant": "第二版参数" if trial_id == PENDING else "第一版参数"},
                       "features": {"synthetic": ["示例特征"]}},
            "series": {"month": ["2024-01", "2024-02", "2024-03", "2024-04"],
                       "nav": [1.0, 1.015, 1.029, 1.047],
                       "excess_nav": [1.0, 1.008, 1.014, 1.025],
                       "hedged_nav": [1.0, 1.011, 1.021, 1.035]},
            "portfolio": {"portfolio": {"note": "【合成演示】虚构组合"},
                          "execution": {"excess_by_year": {"2024": 0.062}},
                          "robustness": {"screens_passed": True, "screens": CHECKS,
                                         "periods": {"all": {"annual_excess": 0.062,
                                                              "information_ratio": ir,
                                                              "tracking_error": 0.043,
                                                              "max_drawdown_excess": -0.077,
                                                              "days": 240}}}},
        })

    conn.execute("INSERT INTO strategy_holdout_requests (request_id, mandate, task_id, trial_id,"
                 " config_hash, status, created_by, created_at, decided_by, decided_at, reason, result_json)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("req-synthetic-approved", "A", TASK, APPROVED, "synthetic-locked-config-v1", "approved",
                  "synthetic-fixture", "2026-09-02T00:00:00Z", "synthetic-reviewer", "2026-09-03T00:00:00Z",
                  "【合成演示】人工批准", json.dumps({"trial_id": HOLDOUT,
                                                   "acceptance": {"passed": True}})))
    conn.execute("INSERT INTO strategy_holdout_requests (request_id, mandate, task_id, trial_id,"
                 " config_hash, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 ("req-synthetic-pending", "A", TASK, PENDING, "synthetic-locked-config-v2", "pending",
                  "synthetic-fixture", "2026-09-04T00:00:00Z"))
    conn.execute("INSERT INTO shortlists (shortlist_id, campaign_id, members_json, l3_json, status, locked_at,"
                 " locked_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("shortlist-synthetic", CAMPAIGN, json.dumps([{"factor_id": "F-000001", "version": 1}]),
                  "{}", "locked", "2026-09-04T00:00:00Z", "synthetic-fixture"))
    conn.execute("INSERT INTO holdout_requests (request_id, shortlist_id, campaign_id, reads_requested, status,"
                 " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 ("req-factor-synthetic-pending", "shortlist-synthetic", CAMPAIGN, 1, "pending",
                  "2026-09-04T00:00:00Z"))
    conn.execute("INSERT INTO agent_requests (request_id, campaign_id, kind, content, status, created_at)"
                 " VALUES (?, ?, ?, ?, ?, ?)",
                 ("request-synthetic", CAMPAIGN, "question", "【合成演示】请选择下一轮研究重点", "open",
                  datetime.now(UTC).isoformat()))
    conn.execute("INSERT INTO review_packets (packet_id, factor_id, version, campaign_id, shortlist_id, artifact_id,"
                 " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 ("packet-synthetic", "F-000001", 1, CAMPAIGN, "shortlist-synthetic", "d" * 24,
                  "open", datetime.now(UTC).isoformat()))

    assert verify_ledger(conn)["ok"]
    conn.close()
    print("【合成演示】看板数据已创建：", ROOT)
    print(f"ALPHASIEVE_HOT_ROOT={ROOT / 'hot'} \\")
    print(f"ALPHASIEVE_STORE_ROOT={ROOT / 'store'} \\")
    print("ALPHASIEVE_STORE_MOUNT='' ALPHASIEVE_ROLE=human ALPHASIEVE_WEB_AUTH=none \\")
    print("uv run alphasieve serve --host 127.0.0.1 --port 8743")


if __name__ == "__main__":
    main()
