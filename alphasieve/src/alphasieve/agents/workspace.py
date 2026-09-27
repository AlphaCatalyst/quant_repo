"""Per-campaign agent workspace: a git repository whose read-only briefing files are regenerated every turn."""

import subprocess
from pathlib import Path

from alphasieve.campaigns import memory, service, stats
from alphasieve.config import Settings
from alphasieve.factors import library as lib
from alphasieve.factors.ops import OPS
from alphasieve.search_space import load_search_space

TEMPLATE = Path(__file__).resolve().parent / "templates" / "program.md"
ARG_NAMES = {"x": "x", "e": "e", "w": "w", "c": "c"}
GENERATED = ("program.md", "brief.md", "memory.md", "directives.md")


def _operators() -> str:
    groups: dict[str, list[str]] = {}
    for name, (_, argspec, kind) in OPS.items():
        args = ", ".join(ARG_NAMES[a] for a in argspec)
        groups.setdefault(kind, []).append(f"`{name}({args})`")
    titles = {"ts": "time series (per stock)", "cs": "cross-section (per date)",
              "group": "industry group (cs_neutralize also removes log size)", "elem": "element-wise"}
    return "\n".join(f"- {titles.get(k, k)}: " + ", ".join(v) for k, v in groups.items())


def render_program(settings: Settings, horizon: int) -> str:
    space = load_search_space(settings)
    terminals = "\n".join(f"- {d}: " + ", ".join(f"`{t}`" for t in fields) for d, fields in space.domains.items())
    text = TEMPLATE.read_text(encoding="utf-8")
    values = {"horizon": horizon, "terminals": terminals, "operators": _operators(),
              "windows": ", ".join(map(str, space.windows)), "max_nodes": space.max_nodes,
              "max_depth": space.max_depth, "max_terminals": space.max_terminals, "forms": ", ".join(space.forms)}
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def render_brief(conn, campaign_id: str, turn_index: int, turn_allowance: int) -> str:
    campaign = service.get_campaign(conn, campaign_id)
    spec = campaign["spec"]
    b = stats.budget_status(conn, campaign_id)
    f = stats.funnel(conn, campaign_id, include_holdout=False)
    remaining = b["trials"]["budget"] - b["trials"]["used"]
    lines = [
        f"# Brief for turn {turn_index}", "",
        f"Campaign: {spec.campaign_id} - {spec.title}", "", f"Question: {spec.question}", "",
        f"- Universe: {spec.universe}; prediction horizon: {spec.horizon} trading days",
        f"- Allowed domains: {', '.join(spec.domains)}",
        "- Focus cells: " + "; ".join(f"{c.domain}/{c.form}/{c.scale}" for c in spec.cells),
        f"- Trials used: {b['trials']['used']} of {b['trials']['budget']} (remaining {remaining})",
        f"- This turn's allowance: at most {min(turn_allowance, remaining)} evaluations",
        f"- Turns used: {b['turns']['used']} of {b['turns']['budget']}; turns without a new L2 pass:"
        f" {b['no_improvement_turns']['current']} (campaign stops at {b['no_improvement_turns']['limit']})",
        "", "## Funnel (distinct candidates reaching each level)", "",
        ", ".join(f"{k}: {v}" for k, v in f["counts"].items() if k in ("submitted", "l0", "l1", "l2")),
        "", "## Most common failed checks", "",
    ]
    for level, reasons in f["failure_reasons"].items():
        if reasons:
            lines.append(f"- {level}: " + ", ".join(f"{k} x{v}" for k, v in list(reasons.items())[:5]))
    lines += ["", "## Library (your candidates must be different from these)", ""]
    for m in lib.library_members(conn):
        lines.append(f"- {m['name']}: `{m['canonical_expression']}` direction {m['direction']},"
                     f" ICIR {m['metrics'].get('icir', float('nan')):.3f}")
    lines += ["", "## Recent evaluations in this campaign", ""]
    for r in stats.recent_outcomes(conn, campaign_id):
        icir = f"{r['icir']:.3f}" if isinstance(r["icir"], (int, float)) else "n/a"
        failed = ", ".join(r["failed"][:3])
        lines.append(f"- `{r['expression']}` -> {r['outcome']} (ICIR {icir}){' failed: ' + failed if failed else ''}")
    return "\n".join(lines) + "\n"


def render_directives(conn, campaign_id: str) -> str:
    rows = [d for d in service.directives(conn, campaign_id) if d["status"] == "pending" or d["kind"] == "forbid"]
    lines = ["# Directives from the researcher", ""]
    if not rows:
        lines.append("None.")
    for d in rows:
        lines.append(f"- [{d['kind']}] {d['content']}")
    return "\n".join(lines) + "\n"


def _git(ws: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(ws), "GIT_AUTHOR_NAME": "alphasieve",
           "GIT_AUTHOR_EMAIL": "alphasieve@localhost", "GIT_COMMITTER_NAME": "alphasieve",
           "GIT_COMMITTER_EMAIL": "alphasieve@localhost"}
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, env=env, check=False)


def prepare(settings: Settings, conn, campaign_id: str, turn_index: int, turn_allowance: int) -> Path:
    ws = settings.workspaces_dir / campaign_id
    for sub in ("candidates", "notes", "reports"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        _git(ws, "init", "-q")
        (ws / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    spec = service.get_campaign(conn, campaign_id)["spec"]
    files = {
        "program.md": render_program(settings, spec.horizon).replace("{turn}", str(turn_index)),
        "brief.md": render_brief(conn, campaign_id, turn_index, turn_allowance),
        "memory.md": memory.render(conn, campaign_id),
        "directives.md": render_directives(conn, campaign_id),
    }
    for name, text in files.items():
        path = ws / name
        if path.exists():
            path.chmod(0o644)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o444)
    return ws


def commit(ws: Path, message: str) -> str | None:
    _git(ws, "add", "-A")
    result = _git(ws, "commit", "-q", "-m", message)
    if result.returncode != 0:
        return None
    return _git(ws, "rev-parse", "--short", "HEAD").stdout.strip()
