"""Template expansion (S-5): one hypothesis, a small parameter grid, many recorded trials.

The first grid point is the default; every other point is recorded as a neighbourhood variant of it
(``params_source: neighborhood``), so L2's neighbourhood limit and L3's trial count both see the search.
Variants are evaluated concurrently when an evaluation queue is live; each thread uses its own connection.
"""

import itertools
import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from alphasieve.contracts import FactorSpec
from alphasieve.errors import AlphaSieveError, validation_error
from alphasieve.state import connect

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
MAX_VARIANTS = 12


class FactorTemplate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    expression: str
    grid: dict[str, list[int | float]]
    hypothesis: str
    cell: dict
    direction: int = 1
    horizon: int = 5
    universe: str = "csi800"


def expand(template: FactorTemplate, limit: int = MAX_VARIANTS) -> list[dict]:
    names = sorted(set(PLACEHOLDER.findall(template.expression)))
    if set(names) != set(template.grid):
        raise validation_error(f"placeholders {names} must match grid keys {sorted(template.grid)}")
    if any(not values for values in template.grid.values()):
        raise validation_error("every grid key needs at least one value (the first is the default)")
    combos = list(itertools.product(*(template.grid[k] for k in names)))
    if len(combos) > limit:
        raise validation_error(f"template expands to {len(combos)} variants; the limit is {limit}")
    specs = []
    for i, values in enumerate(combos):
        params = dict(zip(names, values, strict=True))
        expr = PLACEHOLDER.sub(lambda m, p=params: str(p[m.group(1)]), template.expression)
        suffix = "_".join(f"{k}{v}" for k, v in params.items()).replace(".", "p")
        specs.append({"name": f"{template.name}_{suffix}"[:64], "expression": expr, "hypothesis": template.hypothesis,
                      "cell": template.cell, "direction": template.direction, "horizon": template.horizon,
                      "universe": template.universe, "params": params, "default": i == 0})
    return specs


def evaluate_template(settings, conn, template: FactorTemplate, campaign_id: str | None, executor=None,
                      limit: int = MAX_VARIANTS, concurrency: int = 8) -> dict:
    from alphasieve.evaluation.evaluate import evaluate_spec

    variants = expand(template, limit)
    fields = ("name", "expression", "hypothesis", "cell", "direction", "horizon", "universe")
    default = variants[0]
    first = evaluate_spec(settings, conn, FactorSpec(**{k: default[k] for k in fields}), campaign_id,
                          executor=executor)
    results = [{"params": default["params"], "default": True, **_brief(first)}]

    def run(variant: dict) -> dict:
        local = connect(settings.state_db)
        try:
            spec = FactorSpec(**{k: variant[k] for k in fields}, params_source="neighborhood",
                              neighborhood_of=first["factor_id"])
            return {"params": variant["params"], "default": False,
                    **_brief(evaluate_spec(settings, local, spec, campaign_id, executor=executor))}
        except AlphaSieveError as exc:
            return {"params": variant["params"], "default": False, "error": {"code": exc.code, "message": exc.message}}
        finally:
            local.close()

    workers = concurrency if executor is not None else 1
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results += list(pool.map(run, variants[1:]))
    return {"template": template.name, "variants": len(variants), "default_factor": first["factor_id"],
            "results": results}


def _brief(result: dict) -> dict:
    m = result.get("metrics", {})
    return {"factor": f"{result['factor_id']}@{result['version']}", "trial_id": result["trial_id"],
            "outcome": result.get("outcome"), "expression": result["canonical"], "ic_mean": m.get("ic_mean"),
            "icir": m.get("icir"), "library_max_abs_corr": m.get("library_max_abs_corr"),
            "marginal_ic": m.get("marginal_ic"), "artifact_id": result.get("artifact_id")}
