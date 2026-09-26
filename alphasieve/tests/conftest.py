import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from fixtures.synth import make_raw  # noqa: E402

from alphasieve.config import PACKAGE_CONFIG_DIR, get_settings  # noqa: E402

TEST_SPLITS = {
    "version": 1,
    "locked": False,
    "universe": "csi800",
    "dev": {"start": "2018-01-01", "end": "2020-06-30"},
    "holdout": {"start": "2020-07-01", "end": "2021-12-31"},
    "fresh": {"start": "2022-01-03"},
}


def write_test_configs(target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PACKAGE_CONFIG_DIR, target)
    (target / "splits.yaml").write_text(yaml.safe_dump(TEST_SPLITS), encoding="utf-8")
    return target


def set_env(monkeypatch, hot: Path, store: Path, config_dir: Path, role: str = "human") -> None:
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(hot))
    monkeypatch.setenv("ALPHASIEVE_STORE_ROOT", str(store))
    monkeypatch.setenv("ALPHASIEVE_STORE_MOUNT", "")
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ALPHASIEVE_ROLE", role)


def pytest_configure(config):
    config.addinivalue_line("markers", "network: needs network access to external data sources")
    config.addinivalue_line("markers", "realdata: needs the real panel under /data/alphasieve")


def pytest_collection_modifyitems(config, items):
    run_network = os.environ.get("ALPHASIEVE_RUN_NETWORK") == "1"
    run_real = os.environ.get("ALPHASIEVE_RUN_REALDATA") == "1"
    for item in items:
        if "network" in item.keywords and not run_network:
            item.add_marker(pytest.mark.skip(reason="set ALPHASIEVE_RUN_NETWORK=1"))
        if "realdata" in item.keywords and not run_real:
            item.add_marker(pytest.mark.skip(reason="set ALPHASIEVE_RUN_REALDATA=1"))


@pytest.fixture
def settings(tmp_path, monkeypatch):
    config_dir = write_test_configs(tmp_path / "configs")
    set_env(monkeypatch, tmp_path / "hot", tmp_path / "store", config_dir)
    return get_settings()


@pytest.fixture(scope="session")
def synth_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("synth")
    info = make_raw(root / "hot" / "data" / "raw" / "baostock")
    write_test_configs(root / "configs")
    return root, info


@pytest.fixture(scope="session")
def built_root(synth_root):
    from alphasieve.config import Settings
    from alphasieve.data.panel import build_panel
    from alphasieve.state import connect

    root, info = synth_root
    settings = Settings(
        hot_root=root / "hot", store_root=root / "store", store_mount=None,
        config_dir=root / "configs", role="system", user="test",
    )
    conn = connect(settings.state_db)
    build_panel(settings, conn)
    conn.close()
    return root, info


@pytest.fixture
def panel_settings(built_root, tmp_path, monkeypatch):
    root, _ = built_root
    hot = tmp_path / "hot"
    (hot / "data").mkdir(parents=True)
    for child in ("panel", "raw", "quality"):
        src = root / "hot" / "data" / child
        if src.exists():
            os.symlink(src, hot / "data" / child)
    set_env(monkeypatch, hot, tmp_path / "store", root / "configs")
    return get_settings()


@pytest.fixture
def synth_info(built_root):
    return built_root[1]
