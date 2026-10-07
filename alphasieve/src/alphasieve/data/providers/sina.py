"""Sina Finance daily bars for CFFEX index futures (the free source AKShare also uses; see docs/data/data-vendors).

Single contracts (e.g. ``IC2212``) are available from 2019-03 on; ``IC0`` is Sina's unadjusted main-contract splice
from 2017-01-17. Fields: d/o/h/l/c/v/p, where ``p`` is open interest (zero in the early IC0 rows).
"""

import json
import re
import time
import urllib.request

import pandas as pd

URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_x=/"
       "InnerFuturesNewService.getDailyKLine?symbol={symbol}")
HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
COLUMNS = {"d": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "p": "oi"}
RETRIES = 4
CALL_GAP_S = 0.8


class SinaError(RuntimeError):
    pass


def daily_bars(symbol: str) -> pd.DataFrame:
    """All daily bars Sina has for ``symbol``; an empty frame when the contract is unknown to Sina."""
    last_exc = None
    for attempt in range(RETRIES):
        time.sleep(CALL_GAP_S * (attempt + 1))
        try:
            req = urllib.request.Request(URL.format(symbol=symbol), headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8", "replace")
        except OSError as exc:
            last_exc = exc
            continue
        match = re.search(r"\((\[.*\])\)", text, re.S)
        if match is None:
            if "(null)" in text:
                return pd.DataFrame(columns=list(COLUMNS.values()))
            last_exc = SinaError(f"unexpected response for {symbol}: {text[:80]!r}")
            continue
        df = pd.DataFrame(json.loads(match.group(1)))[list(COLUMNS)].rename(columns=COLUMNS)
        for c in ("open", "high", "low", "close", "volume", "oi"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    raise SinaError(f"{symbol}: {last_exc}")
