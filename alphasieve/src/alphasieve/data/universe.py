from alphasieve.config import Settings, load_config
from alphasieve.errors import validation_error

DEFAULT_UNIVERSE = "csi800"


def universes(settings: Settings) -> dict:
    return {k: v for k, v in load_config(settings, "universes").items() if isinstance(v, dict)}


def universe_config(settings: Settings, name: str | None) -> dict:
    name = name or DEFAULT_UNIVERSE
    table = universes(settings)
    if name not in table:
        raise validation_error(f"unknown universe {name}", allowed=sorted(table))
    return {"name": name, **table[name]}
