"""Read-only overlap audit of exchange and westock margin raw files, 2019-2022."""

import argparse
import json
from pathlib import Path

import pandas as pd

FIELDS = ("FinanceValue", "SecurityValue", "FinanceBuyValue", "FinanceRefundValue", "TradingValue")
START, END = "2019-01-01", "2022-12-31"


def compare(raw_root: Path) -> dict:
    exchange_root = raw_root / "exchange" / "margin"
    westock_root = raw_root / "westock" / "margin"
    matches = []
    for path in sorted(exchange_root.glob("*.parquet")):
        if not START <= path.stem <= END:
            continue
        other = westock_root / path.name
        if other.exists():
            ex = pd.read_parquet(path, columns=["code", *FIELDS])
            ws = pd.read_parquet(other, columns=["code", *FIELDS])
            joined = ex.merge(ws, on="code", suffixes=("_exchange", "_westock"))
            joined["date"] = path.stem
            matches.append(joined)
    if not matches:
        return {"days": 0, "overlap_rows": 0, "fields": {}}
    rows = pd.concat(matches, ignore_index=True)
    fields = {}
    for market, group in rows.groupby(rows["code"].str[:2]):
        fields[market] = {}
        for field in FIELDS:
            left, right = group[f"{field}_exchange"], group[f"{field}_westock"]
            valid = left.notna() & right.notna()
            diff = (left[valid] - right[valid]).abs()
            fields[market][field] = {"pairs": int(valid.sum()), "exact": int((diff < 0.5).sum()),
                                     "max_abs_yuan": float(diff.max()) if len(diff) else None}
    return {"days": len(matches), "overlap_rows": len(rows), "fields": fields}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compare(args.raw_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
