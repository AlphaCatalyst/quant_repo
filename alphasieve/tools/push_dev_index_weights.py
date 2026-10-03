"""Copy DoltHub historical index weights to the platform raw dir, truncated to the dev window (D-40).

    uv run python tools/push_dev_index_weights.py --remote-root /taijifs_zw35/r2/felixjjiang/alphasieve
"""

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from alphasieve.config import get_settings


def push(raw_dir: Path, remote_root: Path, dev_end: str) -> dict:
    src_dir = raw_dir / "dolthub" / "index_weights"
    dst_dir = remote_root / "hot" / "data" / "raw" / "dolthub" / "index_weights"
    dst_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for src in sorted(src_dir.glob("*.parquet")):
        df = pd.read_parquet(src)
        df = df[pd.to_datetime(df["trade_date"]) <= pd.Timestamp(dev_end)]
        if pd.to_datetime(df["trade_date"]).max() > pd.Timestamp(dev_end):
            raise RuntimeError(f"{src.name} still has rows after {dev_end}")
        tmp = dst_dir / f".{src.name}.tmp"
        df.to_parquet(tmp, index=False)
        tmp.replace(dst_dir / src.name)
        out[src.stem] = {"rows": len(df), "last": str(pd.to_datetime(df["trade_date"]).max().date())}
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-root", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    dev_end = yaml.safe_load((settings.config_dir / "splits.yaml").read_text())["dev"]["end"]
    print(json.dumps(push(settings.raw_dir, args.remote_root, dev_end), indent=2))
