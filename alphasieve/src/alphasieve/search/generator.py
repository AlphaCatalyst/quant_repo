"""Programmatic factor search (S-7): random and evolutionary generation inside a campaign's search space.

Every evaluation goes through ``evaluate_spec`` and is recorded in the campaign's own ledger, so L3 discounts the
programmatic search by its own trial count, separately from LLM campaigns. Candidates are validated and
de-duplicated (canonical hash) before evaluation, so invalid or repeated expressions do not consume trials.
"""

import random
import threading
from concurrent.futures import ThreadPoolExecutor

from alphasieve.campaigns import lifecycle, service
from alphasieve.contracts import FactorSpec
from alphasieve.errors import AlphaSieveError
from alphasieve.factors.dsl import DSLError, compile_expression
from alphasieve.search_space import load_search_space
from alphasieve.state import connect

TS_UNARY = ["ts_mean", "ts_std", "ts_sum", "ts_rank", "ts_delta", "ts_zscore", "ts_decay_linear", "ts_max", "ts_min"]
CS_UNARY = ["cs_rank", "cs_zscore", "group_rank"]
BINARY = ["add", "sub", "mul", "div"]
MAX_WINDOW = 120


class Generator:
    def __init__(self, terminals: list[str], windows: list[int], seed: int = 0):
        self.terminals = terminals
        self.windows = [w for w in windows if w <= MAX_WINDOW] or windows
        self.rng = random.Random(seed)

    def expr(self, depth: int = 3) -> str:
        r = self.rng.random()
        if depth <= 0 or r < 0.25:
            return self.rng.choice(self.terminals)
        if r < 0.6:
            return f"{self.rng.choice(TS_UNARY)}({self.expr(depth - 1)}, {self.rng.choice(self.windows)})"
        if r < 0.8:
            return f"{self.rng.choice(CS_UNARY)}({self.expr(depth - 1)})"
        return f"{self.rng.choice(BINARY)}({self.expr(depth - 1)}, {self.expr(depth - 1)})"

    def mutate(self, expr: str) -> str:
        choice = self.rng.random()
        if choice < 0.3:
            return f"neg({expr})"
        if choice < 0.6:
            return f"{self.rng.choice(CS_UNARY)}({expr})"
        if choice < 0.8:
            return f"{self.rng.choice(TS_UNARY)}({expr}, {self.rng.choice(self.windows)})"
        return f"{self.rng.choice(BINARY)}({expr}, {self.expr(1)})"

    def crossover(self, a: str, b: str) -> str:
        return f"{self.rng.choice(['add', 'sub', 'mul'])}(cs_rank({a}), cs_rank({b}))"


def _cell_for(space, compiled) -> dict:
    domains = sorted({d for t in compiled.terminals if (d := space.domain_of(t))})
    scale = space.infer_scale(compiled.max_window, compiled.terminals) or "medium"
    form = "conditional_interaction" if len(domains) > 1 else "level"
    return {"domain": domains[0] if domains else "price", "form": form, "scale": scale}


def run_search(settings, campaign_id: str, trials: int, method: str = "random", population: int = 24,
               concurrency: int = 8, seed: int = 0, executor=None, conclude: bool = True) -> dict:
    conn = connect(settings.state_db)
    campaign = service.get_campaign(conn, campaign_id)
    spec = campaign["spec"]
    if not any(a.harness == "program" for a in spec.agents):
        raise AlphaSieveError("VALIDATION_ERROR", "programmatic search needs a campaign whose agents include "
                              "harness 'program' (separate accounting from LLM campaigns)")
    space = load_search_space(settings)
    terminals = [t for d in spec.domains for t in space.domains.get(d, ())]
    gen = Generator(terminals, list(space.windows), seed)
    seen = {r[0] for r in conn.execute("SELECT DISTINCT candidate_hash FROM trials WHERE campaign_id = ?",
                                       (campaign_id,))}
    lock = threading.RLock()
    scored: list[tuple[float, str]] = []
    counter = {"n": 0}

    def candidate() -> tuple[str, object] | None:
        for _ in range(200):
            if method == "evolve" and len(scored) >= population // 2:
                elite = [e for _, e in sorted(scored, reverse=True)[:population // 2]]
                expr = gen.crossover(*gen.rng.sample(elite, 2)) if gen.rng.random() < 0.3 and len(elite) > 1 \
                    else gen.mutate(gen.rng.choice(elite))
            else:
                expr = gen.expr()
            try:
                compiled = compile_expression(expr, space)
            except DSLError:
                continue
            with lock:
                if compiled.candidate_hash in seen:
                    continue
                seen.add(compiled.candidate_hash)
            return expr, compiled
        return None

    def evaluate_one(_i: int) -> dict:
        with lock:
            gen_state = candidate()
        if gen_state is None:
            return {"skipped": "no new valid candidate"}
        expr, compiled = gen_state
        with lock:
            counter["n"] += 1
            name = f"prog_{campaign_id.replace('-', '_')[:30]}_{counter['n']:05d}"
        local = connect(settings.state_db)
        try:
            fs = FactorSpec(name=name, expression=expr, hypothesis=f"programmatic search ({method})",
                            cell=_cell_for(space, compiled), direction=1, horizon=spec.horizon,
                            universe=spec.universe)
            result = lifecycle_eval(settings, local, fs, campaign_id, executor)
            icir = result.get("metrics", {}).get("icir")
            if isinstance(icir, (int, float)):
                with lock:
                    scored.append((float(icir), expr))
            return {"expression": expr, "outcome": result.get("outcome"), "icir": icir}
        except AlphaSieveError as exc:
            return {"expression": expr, "error": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"expression": expr, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=max(1, concurrency if executor is not None else 1)) as pool:
        results = list(pool.map(evaluate_one, range(trials)))
    outcomes: dict[str, int] = {}
    for r in results:
        key = r.get("outcome") or r.get("error") or r.get("skipped")
        outcomes[key] = outcomes.get(key, 0) + 1
    out = {"campaign_id": campaign_id, "method": method, "requested": trials, "outcomes": outcomes,
           "best": sorted(scored, reverse=True)[:5]}
    if conclude and service.get_campaign(conn, campaign_id)["status"] == "running":
        from alphasieve.campaigns.stats import stop_reason
        reason = stop_reason(conn, campaign_id)
        if reason == "trial_budget_exhausted":
            out["conclusion"] = lifecycle.conclude(conn, settings, campaign_id, reason)
    conn.close()
    return out


def lifecycle_eval(settings, conn, spec: FactorSpec, campaign_id: str, executor) -> dict:
    from alphasieve.evaluation.evaluate import evaluate_spec

    return evaluate_spec(settings, conn, spec, campaign_id, executor=executor)
