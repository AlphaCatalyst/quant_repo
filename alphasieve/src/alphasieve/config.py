import getpass
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from alphasieve.errors import AlphaSieveError, validation_error

ROLES = ("agent", "human", "system")
TIERS = ("dev", "holdout", "fresh")

DEFAULT_HOT_ROOT = "/data/alphasieve"
DEFAULT_STORE_ROOT = "/mnt/private_felixjjiang/alphasieve"
DEFAULT_STORE_MOUNT = "/mnt/private_felixjjiang"
PACKAGE_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


@dataclass(frozen=True)
class Settings:
    hot_root: Path
    store_root: Path
    store_mount: Path | None
    config_dir: Path
    role: str
    user: str
    campaign: str | None = None
    trial_ceiling: int | None = None

    @property
    def state_db(self) -> Path:
        return self.hot_root / "state" / "alphasieve.db"

    @property
    def raw_dir(self) -> Path:
        return self.hot_root / "data" / "raw"

    def panel_dir(self, tier: str, universe: str | None = None) -> Path:
        if tier not in TIERS:
            raise validation_error(f"unknown evidence tier {tier}")
        if universe in (None, "csi800"):
            return self.hot_root / "data" / "panel" / tier
        return self.hot_root / "data" / "panel" / universe / tier

    @property
    def quality_dir(self) -> Path:
        return self.hot_root / "data" / "quality"

    @property
    def cache_dir(self) -> Path:
        return self.hot_root / "cache"

    @property
    def artifacts_dir(self) -> Path:
        return self.store_root / "artifacts"

    @property
    def raw_store_dir(self) -> Path:
        return self.store_root / "raw"

    @property
    def reports_dir(self) -> Path:
        return self.store_root / "reports"

    @property
    def workspaces_dir(self) -> Path:
        return self.hot_root / "workspaces"

    @property
    def transcripts_dir(self) -> Path:
        return self.store_root / "transcripts"


def get_settings(role: str | None = None) -> Settings:
    role = role or os.environ.get("ALPHASIEVE_ROLE", "human")
    if role not in ROLES:
        raise validation_error(f"ALPHASIEVE_ROLE must be one of {ROLES}, got {role!r}")
    store_root = os.environ.get("ALPHASIEVE_STORE_ROOT", DEFAULT_STORE_ROOT)
    if "ALPHASIEVE_STORE_MOUNT" in os.environ:
        mount = os.environ["ALPHASIEVE_STORE_MOUNT"] or None
    else:
        mount = DEFAULT_STORE_MOUNT if store_root.startswith(DEFAULT_STORE_MOUNT) else None
    return Settings(
        hot_root=Path(os.environ.get("ALPHASIEVE_HOT_ROOT", DEFAULT_HOT_ROOT)),
        store_root=Path(store_root),
        store_mount=Path(mount) if mount else None,
        config_dir=Path(os.environ.get("ALPHASIEVE_CONFIG_DIR", PACKAGE_CONFIG_DIR)),
        role=role,
        user=os.environ.get("ALPHASIEVE_USER", getpass.getuser()),
        campaign=os.environ.get("ALPHASIEVE_CAMPAIGN") or None,
        trial_ceiling=int(ceiling) if (ceiling := os.environ.get("ALPHASIEVE_TRIAL_CEILING")) else None,
    )


def ensure_storage(settings: Settings, need_store: bool = True) -> None:
    for path in (settings.state_db.parent, settings.raw_dir, settings.cache_dir, settings.quality_dir):
        path.mkdir(parents=True, exist_ok=True)
    if not need_store:
        return
    if settings.store_mount is not None and not os.path.ismount(settings.store_mount):
        raise AlphaSieveError(
            "STORAGE_UNAVAILABLE",
            f"store mount {settings.store_mount} is not mounted",
            {"store_root": str(settings.store_root)},
        )
    for path in (settings.artifacts_dir, settings.raw_store_dir, settings.reports_dir):
        path.mkdir(parents=True, exist_ok=True)


def load_config(settings: Settings, name: str) -> dict:
    path = settings.config_dir / f"{name}.yaml"
    if not path.exists():
        raise validation_error(f"config {name} not found", path=str(path))
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "version" not in data:
        raise validation_error(f"config {name} has no version", path=str(path))
    return data
