import io

import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.data.panel import attach_sw_industry
from alphasieve.data.providers.swsresearch import infer_current_names, load_official_names, parse_history, parse_names
from alphasieve.data.sync import sync_sw_industry_hist
from alphasieve.state import connect


def _workbook() -> bytes:
    source = pd.DataFrame({"股票代码": [1, 1, 600000],
                           "计入日期": ["2014-02-21", "2021-07-30", "2014-02-21"],
                           "行业代码": [480101, 480301, 480101],
                           "更新日期": ["2024-09-27", "2025-12-15", "2014-02-21"]})
    output = io.BytesIO()
    source.to_excel(output, index=False)
    return output.getvalue()


def _names_workbook(rows):
    output = io.BytesIO()
    pd.DataFrame(rows, columns=["行业代码", "一级行业名称", "二级行业名称", "三级行业名称"]).to_excel(
        output, index=False)
    return output.getvalue()


def test_official_names_and_versioned_history(tmp_path):
    old_data = _names_workbook([
        (480000, "银行旧", None, None), (480100, "银行旧", "旧二级", None),
        (480101, "银行旧", "旧二级", "旧三级"),
    ])
    new_data = _names_workbook([
        (480000, "银行新", None, None), (480100, "银行新", "新二级", None),
        (480101, "银行新", "新二级", "新三级"),
        (480300, "银行新", "另二级", None), (480301, "银行新", "另二级", "另三级"),
    ])
    (tmp_path / "SwClassCode_2014_20261003.xls").write_bytes(old_data)
    (tmp_path / "SwClassCode_2021_20261003.xls").write_bytes(new_data)
    names = load_official_names(tmp_path, "20261003")
    assert len(names) == 8
    assert names.loc[names.sw_code.eq("480100") & names.sw_version.eq("SW2014"), "name"].iloc[0] == "旧二级"
    history = parse_history(_workbook(), names)
    assert history["sw_version"].tolist() == ["SW2014", "SW2014", "SW2021"]
    assert history["l2_name"].tolist() == ["旧二级", "旧二级", "另二级"]
    assert history["l3_name"].tolist() == ["旧三级", "旧三级", "另三级"]
    assert history["history_source_sha256"].str.len().eq(64).all()
    assert history["name_source_sha256"].str.len().eq(64).all()


def test_unknown_legacy_code_is_not_given_a_current_name():
    source = pd.DataFrame({"股票代码": [1], "计入日期": ["1991-01-01"],
                           "行业代码": [440101], "更新日期": ["1991-01-01"]})
    output = io.BytesIO()
    source.to_excel(output, index=False)
    names = parse_names(_names_workbook([(480000, "银行", None, None)]), "SW2021")
    history = parse_history(output.getvalue(), names)
    assert history.loc[0, "sw_version"] == "unknown"
    assert pd.isna(history.loc[0, "l1_name"])
    assert pd.isna(history.loc[0, "l2_name"])


def test_parse_and_availability():
    names = pd.DataFrame({"sw_code": ["480000", "480100", "480300", "480101", "480301"],
                          "name": ["银行", "银行甲", "银行乙", "甲", "乙"]})
    history = parse_history(_workbook(), names)
    assert history.code.tolist() == ["sh.600000", "sz.000001", "sz.000001"]
    assert history.loc[1, "l1_name"] == "银行"
    panel = pd.DataFrame({"date": pd.to_datetime(["2024-09-27", "2024-09-30", "2025-12-15",
                                                      "2025-12-16"]), "code": ["sz.000001"] * 4,
                          "industry": ["证监会银行"] * 4})
    calendar = ["2024-09-27", "2024-09-30", "2025-12-15", "2025-12-16"]
    result = attach_sw_industry(panel, history, calendar)
    assert result.industry.eq("证监会银行").all()
    assert result.sw2.tolist() == ["unknown", "银行甲", "银行甲", "银行乙"]
    restated = attach_sw_industry(panel, history, calendar, conservative=False)
    assert restated.sw2.tolist() == ["银行乙"] * 4


def test_inferred_names_require_unanimity():
    history = pd.DataFrame({"code": ["sz.000001", "sh.600000"], "effective_date": ["2020-01-01"] * 2,
                            "l1_code": ["480000"] * 2, "l2_code": ["480100"] * 2,
                            "l3_code": ["480101"] * 2})
    snapshot = pd.DataFrame({"code": ["sz.000001", "sh.600000"], "level": [1, 1],
                             "sector_name": ["银行", "银行"]})
    assert infer_current_names(history, snapshot).iloc[0].to_dict() == {
        "sw_code": "480000", "name": "银行", "source": "westock_membership_2026"}
    snapshot.loc[1, "sector_name"] = "金融"
    assert infer_current_names(history, snapshot).empty


def test_panel_industry_asof_uses_requested_day():
    panel = Panel(pd.DataFrame({"date": pd.to_datetime(["2020-01-02", "2020-01-03"]),
                                "code": ["sz.000001"] * 2,
                                "industry": ["证监会", "证监会"],
                                "sw1": ["unknown", "银行"]}), {"tier": "dev", "signature": "test"})
    assert panel.industry_asof("2020-01-02", "sw1").iloc[0] == "unknown"
    assert panel.industry_asof("2020-01-03", "sw1").iloc[0] == "银行"


def test_sync_uses_cached_file_when_official_host_fails(settings, monkeypatch):
    from alphasieve.data.providers import swsresearch

    root = settings.raw_dir / "swsresearch"
    root.mkdir(parents=True)
    (root / "StockClassifyUse_stock_20261002.xls").write_bytes(_workbook())

    def fail(_url):
        raise swsresearch.free_http.FreeDataError("offline")

    monkeypatch.setattr(swsresearch, "fetch", fail)
    conn = connect(settings.state_db)
    try:
        result = sync_sw_industry_hist(settings, conn, "2026-10-03")
        assert result["stale"] is True
        assert result["rows"] == 3
        assert (root / "sw_industry_hist_20261003.parquet").exists()
    finally:
        conn.close()


def test_sync_names_from_cached_official_code_books(settings, monkeypatch):
    from alphasieve.data.providers import swsresearch

    root = settings.raw_dir / "swsresearch"
    root.mkdir(parents=True)
    (root / "StockClassifyUse_stock_20261003.xls").write_bytes(_workbook())
    (root / "SwClassCode_2014_20261001.xls").write_bytes(_names_workbook([
        (480000, "银行旧", None, None), (480100, "银行旧", "旧二级", None),
        (480101, "银行旧", "旧二级", "旧三级")]))
    (root / "SwClassCode_2021_20261001.xls").write_bytes(_names_workbook([
        (480000, "银行新", None, None), (480300, "银行新", "另二级", None),
        (480301, "银行新", "另二级", "另三级")]))

    def fail(_url):
        raise swsresearch.free_http.FreeDataError("offline")

    monkeypatch.setattr(swsresearch, "fetch", fail)
    conn = connect(settings.state_db)
    try:
        result = sync_sw_industry_hist(settings, conn, "2026-10-03")
        history = pd.read_parquet(root / "sw_industry_hist.parquet")
        assert result["l2_named"] == 3
        assert history["l2_name"].tolist() == ["旧二级", "旧二级", "另二级"]
    finally:
        conn.close()
