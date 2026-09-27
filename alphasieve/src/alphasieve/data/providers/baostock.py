import contextlib
import io
import time

import pandas as pd

DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,isST"
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
NUMERIC_DAILY = [
    "open", "high", "low", "close", "preclose", "volume", "amount", "turn", "tradestatus", "pctChg",
    "peTTM", "pbMRQ", "psTTM", "isST",
]
NUMERIC_INDEX = ["open", "high", "low", "close", "preclose", "volume", "amount", "pctChg"]
NUMERIC_PROFIT = ["roeAvg", "npMargin", "gpMargin", "netProfit", "epsTTM", "MBRevenue", "totalShare", "liqaShare"]
NUMERIC_GROWTH = ["YOYEquity", "YOYAsset", "YOYNI", "YOYEPSBasic", "YOYPNI"]
INDEX_MEMBER_QUERIES = {"hs300": "query_hs300_stocks", "zz500": "query_zz500_stocks"}


class ProviderError(RuntimeError):
    pass


def _to_frame(rs) -> pd.DataFrame:
    if rs.error_code != "0":
        raise ProviderError(f"baostock error {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def _numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].replace("", None), errors="coerce")
    return df


class BaoStockSession:
    """Single login session; baostock keeps one global socket per process."""

    def __init__(self, retries: int = 3, pause: float = 2.0):
        import baostock

        self.bs = baostock
        self.retries = retries
        self.pause = pause

    def __enter__(self):
        self._login()
        return self

    def __exit__(self, *exc):
        with contextlib.suppress(Exception), contextlib.redirect_stdout(io.StringIO()):
            self.bs.logout()

    def _login(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.bs.login()
        if result.error_code != "0":
            raise ProviderError(f"baostock login failed: {result.error_msg}")

    def query(self, method: str, **kwargs) -> pd.DataFrame:
        last_error = None
        for attempt in range(self.retries):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    return _to_frame(getattr(self.bs, method)(**kwargs))
            except Exception as exc:  # noqa: BLE001  (network errors surface as varied types)
                last_error = exc
                time.sleep(self.pause * (attempt + 1))
                with contextlib.suppress(Exception):
                    self._login()
        raise ProviderError(f"{method}({kwargs}) failed after {self.retries} attempts: {last_error}")

    def trade_dates(self, start: str, end: str) -> pd.DataFrame:
        df = self.query("query_trade_dates", start_date=start, end_date=end)
        df["is_trading_day"] = df["is_trading_day"].astype(int)
        return df

    def stock_basic(self) -> pd.DataFrame:
        return self.query("query_stock_basic")

    def industry(self) -> pd.DataFrame:
        return self.query("query_stock_industry")

    def index_members(self, index: str, date: str) -> pd.DataFrame:
        df = self.query(INDEX_MEMBER_QUERIES[index], date=date)
        df["snapshot_date"] = date
        df["index"] = index
        return df

    def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        df = self.query(
            "query_history_k_data_plus", code=code, fields=DAILY_FIELDS,
            start_date=start, end_date=end, frequency="d", adjustflag="3",
        )
        return _numeric(df, NUMERIC_DAILY)

    def adjust_factor(self, code: str, end: str) -> pd.DataFrame:
        df = self.query("query_adjust_factor", code=code, start_date="1990-01-01", end_date=end)
        return _numeric(df, ["foreAdjustFactor", "backAdjustFactor", "adjustFactor"])

    def index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        df = self.query(
            "query_history_k_data_plus", code=code, fields=INDEX_FIELDS,
            start_date=start, end_date=end, frequency="d",
        )
        return _numeric(df, NUMERIC_INDEX)

    def profit(self, code: str, year: int, quarter: int) -> pd.DataFrame:
        return _numeric(self.query("query_profit_data", code=code, year=year, quarter=quarter), NUMERIC_PROFIT)

    def growth(self, code: str, year: int, quarter: int) -> pd.DataFrame:
        return _numeric(self.query("query_growth_data", code=code, year=year, quarter=quarter), NUMERIC_GROWTH)

    def forecast(self, code: str, start: str, end: str) -> pd.DataFrame:
        df = self.query("query_forecast_report", code=code, start_date=start, end_date=end)
        return _numeric(df, ["profitForcastChgPctUp", "profitForcastChgPctDwn"])

    def express(self, code: str, start: str, end: str) -> pd.DataFrame:
        df = self.query("query_performance_express_report", code=code, start_date=start, end_date=end)
        return _numeric(df, [c for c in df.columns if c.startswith("performanceExpress")])

    def minute(self, code: str, start: str, end: str, frequency: str = "5") -> pd.DataFrame:
        df = self.query("query_history_k_data_plus", code=code, fields="date,time,open,high,low,close,volume,amount",
                        start_date=start, end_date=end, frequency=frequency, adjustflag="3")
        return _numeric(df, ["open", "high", "low", "close", "volume", "amount"])
