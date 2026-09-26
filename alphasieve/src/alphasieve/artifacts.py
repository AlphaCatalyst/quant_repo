import json
import os
import shutil
import tempfile
from pathlib import Path

from alphasieve.config import Settings
from alphasieve.util import canonical_json, pretty_json, sha256_hex

ARTIFACT_ID_LENGTH = 24


def artifact_id_for(manifest: dict) -> str:
    return sha256_hex(canonical_json(manifest))[:ARTIFACT_ID_LENGTH]


def write_artifact(
    settings: Settings,
    manifest: dict,
    metrics: dict | None = None,
    report: str | None = None,
    charts: dict[str, dict] | None = None,
) -> str:
    artifact_id = artifact_id_for(manifest)
    target = settings.artifacts_dir / artifact_id
    if target.exists():
        return artifact_id
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=settings.artifacts_dir))
    try:
        (tmp / "manifest.json").write_text(pretty_json(manifest), encoding="utf-8")
        if metrics is not None:
            (tmp / "metrics.json").write_text(pretty_json(metrics), encoding="utf-8")
        if report is not None:
            (tmp / "report.md").write_text(report, encoding="utf-8")
        for name, chart in (charts or {}).items():
            (tmp / "charts").mkdir(exist_ok=True)
            (tmp / "charts" / f"{name}.json").write_text(pretty_json(chart), encoding="utf-8")
        os.rename(tmp, target)
    except FileExistsError:
        shutil.rmtree(tmp, ignore_errors=True)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if not target.exists():
            raise
    return artifact_id


def read_artifact_manifest(settings: Settings, artifact_id: str) -> dict:
    return json.loads((settings.artifacts_dir / artifact_id / "manifest.json").read_text(encoding="utf-8"))
