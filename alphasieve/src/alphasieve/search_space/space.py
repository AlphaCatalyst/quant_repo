from dataclasses import dataclass

from alphasieve.config import Settings, load_config


@dataclass(frozen=True)
class SearchSpace:
    search_space_id: str
    version: int
    windows: tuple[int, ...]
    max_nodes: int
    max_depth: int
    max_terminals: int
    domains: dict[str, tuple[str, ...]]
    forms: tuple[str, ...]
    scales: tuple[str, ...]
    scale_bounds: dict[str, int]
    derived_version: str
    default_horizon: dict[str, int] | None = None

    @property
    def version_tag(self) -> str:
        return f"{self.search_space_id}@{self.version}"

    @property
    def terminals(self) -> set[str]:
        return {t for fields in self.domains.values() for t in fields}

    def horizon_for(self, domains: list[str]) -> int:
        horizons = [self.default_horizon.get(d, 5) for d in domains] if self.default_horizon else []
        return max(horizons) if horizons else 5

    def domain_of(self, terminal: str) -> str | None:
        for domain, fields in self.domains.items():
            if terminal in fields:
                return domain
        return None

    def infer_scale(self, max_window: int | None, terminals: set[str]) -> str | None:
        if max_window is None:
            financial = {t for t in terminals if self.domain_of(t) in ("profitability", "growth")}
            return "quarterly" if financial and financial == terminals else None
        for scale in ("short", "medium", "long"):
            if max_window <= self.scale_bounds[scale]:
                return scale
        return "long"

    def check_cell(self, cell: dict, terminals: set[str], max_window: int | None) -> dict:
        domains = sorted({d for t in terminals if (d := self.domain_of(t))})
        scale = self.infer_scale(max_window, terminals)
        warnings = []
        if cell["domain"] not in self.domains:
            warnings.append(f"declared domain {cell['domain']!r} is not in the search space")
        elif domains and cell["domain"] not in domains:
            warnings.append(f"declared domain {cell['domain']!r} but expression uses {domains}")
        if cell["form"] not in self.forms:
            warnings.append(f"declared form {cell['form']!r} is not in the search space")
        if scale is not None and cell["scale"] != scale:
            warnings.append(f"declared scale {cell['scale']!r} but inferred {scale!r} from windows")
        recorded = {
            "domain": cell["domain"] if not domains or cell["domain"] in domains else domains[0],
            "form": cell["form"],
            "scale": scale or cell["scale"],
        }
        return {"declared": cell, "inferred_domains": domains, "inferred_scale": scale, "recorded": recorded,
                "warnings": warnings}


def load_search_space(settings: Settings) -> SearchSpace:
    cfg = load_config(settings, "search_space")
    return SearchSpace(
        search_space_id=cfg["search_space_id"],
        version=cfg["version"],
        windows=tuple(cfg["windows"]),
        max_nodes=cfg["complexity"]["max_nodes"],
        max_depth=cfg["complexity"]["max_depth"],
        max_terminals=cfg["complexity"]["max_terminals"],
        domains={k: tuple(v) for k, v in cfg["domains"].items()},
        forms=tuple(cfg["forms"]),
        scales=tuple(cfg["scales"]),
        scale_bounds=dict(cfg["scale_bounds"]),
        derived_version=cfg["derived_version"],
        default_horizon=dict(cfg.get("default_horizon") or {}),
    )
