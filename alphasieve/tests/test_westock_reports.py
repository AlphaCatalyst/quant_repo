"""Human-labelled, pre-2023 westock report samples and parser boundaries."""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from alphasieve.data import reports

FIXTURES = Path(__file__).parent / "fixtures" / "westock_reports_dev.jsonl"
SAMPLES = [json.loads(line) for line in FIXTURES.read_text().splitlines()]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda x: f"dev-{x['source_index']:02d}")
def test_human_labelled_dev_report(sample):
    rows = reports.normalize_report(
        sample["report_id"], sample["code"], sample["list_time"], sample["detail"], sample["list_title"]
    )
    actual = {row["fiscal_year"]: row["eps"] for row in rows if row["fiscal_year"] is not None}
    expected = {int(year): value for year, value in sample["expected_eps"].items()}
    assert actual == expected
    assert bool(rows) == sample["expect_report"]
    if rows:
        assert all(row["publish_date"] <= "2022-12-31" for row in rows)
        assert all(row["code"].count(".") == 1 for row in rows)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("维持 2013-2015 年 0.43/0.58/0.68 元 EPS 预测", {2013: 0.43, 2014: 0.58, 2015: 0.68}),
        ("盈利预测：2012-2014 可实现 EPS 分别为 0.2、0.3、0.4 元", {2012: 0.2, 2013: 0.3, 2014: 0.4}),
        ("预计未来增长。2019-2021 公司收入回升。我们预测 EPS 分别为 0.5/0.6/0.7 元", {2019: 0.5, 2020: 0.6, 2021: 0.7}),
        ("公司实现 EPS 0.88 元，基本每股收益 0.98 元。维持买入评级", {}),
        ("预计分业务 EPS 2019-2021 年分别为 0.1/0.2/0.3 元", {}),
        ("预计 2019-2021 年 EPS 分别为 0.35(原 0.25)/0.45(原 0.30)/0.55 元", {2019: 0.35, 2020: 0.45, 2021: 0.55}),
    ],
)
def test_known_parsing_boundaries(text, expected):
    eps, _, _ = reports.parse_text(text)
    assert eps == expected


def test_media_empty_body_and_misattached_codes_rejected():
    base = {"jgmc": "某证券", "title": "甲公司(600000)点评", "detail": "预计2021年EPS为0.5元，给予买入评级。"}
    assert not reports.normalize_report("x", "sh600000", "2021-01-01", {}, "【某证券】甲公司(600000)")
    assert not reports.normalize_report("x", "sh600000", "2021-01-01", base, "【某财经网】甲公司(600000)")
    assert not reports.normalize_report("x", "sh600000", "2021-01-01", base, "【某证券】乙公司(600001)")
    assert not reports.normalize_report(
        "x", "sh600000", "2021-01-01", {**base, "title": "甲公司点评", "detail": "乙公司(600001)预计2021年EPS为0.5元"}
    )
    rows = reports.normalize_report("x", "sh600000", "2021-01-01", base, "【某证券】甲公司(600000)")
    assert rows[0]["rating"] == "buy" and rows[0]["rating_raw"] == "买入"


def test_read_only_sqlite_and_atomic_materialization(tmp_path):
    db = tmp_path / "reports.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            "CREATE TABLE details (id TEXT PRIMARY KEY, code TEXT, list_time TEXT, detail_json TEXT, fetched_at TEXT);"
        )
        detail = {"jgmc": "某证券", "title": "甲公司(600000)点评", "detail": "预计2021年EPS为0.5元，给予买入评级。"}
        conn.executemany(
            "INSERT INTO details VALUES (?,?,?,?,?)",
            [
                ("old", "sh600000", "2021-01-01", json.dumps(detail), "2021-01-02"),
                ("future", "sh600000", "2023-01-01", "not JSON", "2023-01-02"),
            ],
        )
    output = tmp_path / "parsed.parquet"
    frame = reports.write_parsed(db, output, latest_date="2022-12-31")
    assert frame["report_id"].tolist() == ["old"]
    assert pd.read_parquet(output)["eps"].tolist() == [0.5]
    assert not list(tmp_path.glob(".parsed-*.parquet"))
