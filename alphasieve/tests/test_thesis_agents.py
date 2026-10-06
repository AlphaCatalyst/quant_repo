"""Single-run thesis agent integration with scripted executors."""

import json
from pathlib import Path

import yaml

from alphasieve.agents.executors import FakeExecutor
from alphasieve.cli.main import main
from alphasieve.config import get_settings
from alphasieve.thesis.agent_run import list_runs, resume_interrupted, run_draft, run_review

SAMPLE = Path(__file__).resolve().parents[1] / "theses" / "hog-cycle-muyuan.yaml"


def _roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(tmp_path / "hot"))
    monkeypatch.setenv("ALPHASIEVE_STORE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("ALPHASIEVE_STORE_MOUNT", "")
    monkeypatch.setenv("ALPHASIEVE_ROLE", "human")
    return get_settings()


def _cli(argv, capsys):
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_draft_validates_and_lists_via_cli(tmp_path, monkeypatch, capsys):
    settings = _roots(tmp_path, monkeypatch)

    def script(ctx, env, run_cli, result):
        assert ctx.profile.name == "researcher"
        (ctx.workspace / "drafts" / "proposal.yaml").write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        return "drafted"

    run = run_draft(settings, "hog cycle", "fake", fake_script=script)
    assert run["status"] == "completed"
    assert run["changed_files"] == ["drafts/proposal.yaml"]
    assert run["drafts"][0]["scenarios"]["base"] > 0
    assert not (Path(__file__).resolve().parents[1] / "theses" / "drafts" / "proposal.yaml").exists()
    assert list_runs(settings)[0]["run_id"] == run["run_id"]
    code, out = _cli(["thesis", "runs", "--limit", "1"], capsys)
    assert code == 0 and out["data"]["runs"][0]["run_id"] == run["run_id"]
    code, out = _cli(["thesis", "run-show", run["run_id"]], capsys)
    assert code == 0 and out["data"]["status"] == "completed"


def test_outside_write_is_rejected(tmp_path, monkeypatch):
    settings = _roots(tmp_path, monkeypatch)

    def script(ctx, env, run_cli, result):
        (ctx.workspace / "brief.md").chmod(0o644)
        (ctx.workspace / "brief.md").write_text("changed", encoding="utf-8")
        return "done"

    run = run_draft(settings, "test", "fake", fake_script=script)
    assert run["status"] == "rejected"
    assert "brief.md" in run["violations"]
    assert list_runs(settings)[0]["status"] == "rejected"


def test_review_and_role_restriction(tmp_path, monkeypatch, capsys):
    settings = _roots(tmp_path, monkeypatch)

    def script(ctx, env, run_cli, result):
        assert ctx.profile.name == "reviewer"
        assert yaml.safe_load((ctx.workspace / "target.yaml").read_text(encoding="utf-8"))["thesis_id"]
        (ctx.workspace / "reviews" / "review.md").write_text(
            "| Claim | Verdict | Evidence URL | Impact on valuation |\n"
            "|---|---|---|---|\n| price | unverified | https://example.org | material |\n"
            "\nScorecard: evidence 2/5, valuation 3/5\n", encoding="utf-8")
        return "reviewed"

    run = run_review(settings, SAMPLE, "fake", fake_script=script)
    assert run["status"] == "completed"
    assert run["changed_files"] == ["reviews/review.md"]
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    for argv in (["thesis", "draft", "--topic", "x"], ["thesis", "review", str(SAMPLE)],
                 ["thesis", "runs"], ["thesis", "run-show", run["run_id"]]):
        code, out = _cli(argv, capsys)
        assert code != 0 and out["error"]["code"] == "PERMISSION_DENIED"


def test_draft_and_review_cli_json_with_fake_executor(tmp_path, monkeypatch, capsys):
    settings = _roots(tmp_path, monkeypatch)

    def script(ctx, env, run_cli, result):
        if ctx.profile.name == "researcher":
            (ctx.workspace / "drafts" / "proposal.yaml").write_text(SAMPLE.read_text(encoding="utf-8"),
                                                                   encoding="utf-8")
        else:
            (ctx.workspace / "reviews" / "review.md").write_text("Scorecard: 3/5\n", encoding="utf-8")
        return "done"

    monkeypatch.setattr("alphasieve.thesis.agent_run.make_executor",
                        lambda _settings, harness, fake_script=None: FakeExecutor(settings, script))
    code, out = _cli(["thesis", "draft", "--topic", "hog cycle"], capsys)
    assert code == 0 and out["data"]["status"] == "completed"
    assert out["data"]["drafts"][0]["thesis_id"] == "hog-cycle-muyuan"
    code, out = _cli(["thesis", "review", str(SAMPLE)], capsys)
    assert code == 0 and out["data"]["changed_files"] == ["reviews/review.md"]


def test_review_without_output_fails(tmp_path, monkeypatch):
    settings = _roots(tmp_path, monkeypatch)

    def script(ctx, env, run_cli, result):
        assert "target.yaml" in (ctx.workspace / "brief.md").read_text(encoding="utf-8")
        return "nothing written"

    run = run_review(settings, SAMPLE, "fake", fake_script=script)
    assert run["status"] == "failed"
    assert "without writing under reviews/" in run["error"]


def test_codex_outage_keeps_workspace_and_resumes_once(tmp_path, monkeypatch):
    settings = _roots(tmp_path, monkeypatch)
    from alphasieve.control.llm import LlmUnavailable

    available = False

    def availability(_settings):
        if not available:
            raise LlmUnavailable()

    def script(ctx, env, run_cli, result):
        (ctx.workspace / "drafts" / "proposal.yaml").write_text(SAMPLE.read_text(encoding="utf-8"))
        return "drafted"

    monkeypatch.setattr("alphasieve.thesis.agent_run.require_available", availability)
    monkeypatch.setattr("alphasieve.thesis.agent_run.make_executor",
                        lambda _settings, harness, fake_script=None: FakeExecutor(settings, script))
    run = run_draft(settings, "hog cycle", "codex", run_id="thesis-outage")
    assert run["status"] == "interrupted"
    assert Path(run["workspace"]).exists()
    assert run["resume_count"] == 0
    available = True
    resumed = resume_interrupted(settings, run["run_id"])
    assert resumed is not None and resumed["status"] == "completed"
    assert resumed["workspace"] == run["workspace"]
    assert resumed["resume_count"] == 1
    assert resume_interrupted(settings, run["run_id"]) is None


def test_claude_network_failure_is_interrupted(tmp_path, monkeypatch):
    settings = _roots(tmp_path, monkeypatch)

    def network_failure(ctx, env, run_cli, result):
        raise ConnectionResetError("connection reset by peer")

    run = run_review(settings, SAMPLE, "fake", fake_script=network_failure)
    assert run["status"] == "interrupted"
    assert list_runs(settings)[0]["status"] == "interrupted"
