"""Yahoo Finance'ten 1 dakikalık mumlar (ücretsiz, resmi olmayan kaynak)."""
import warnings
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

NY = ZoneInfo("America/New_York")

# yfinance'in her çekişte bastığı pandas uyarıları logu boğmasın
warnings.filterwarnings("ignore", module="yfinance")


def market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(NY)
    # 16:00 mumunun kapanışını da almak için 1 dk pay
    return now.weekday() < 5 and time(9, 30) <= now.time() <= time(16, 1)


def fetch(tickers: list[str], period: str = "1d", interval: str = "1m") -> dict[str, pd.DataFrame]:
    df = yf.download(
        tickers, period=period, interval=interval, group_by="ticker",
        prepost=False, auto_adjust=False, progress=False, threads=True,
    )
    out = {}
    for t in tickers:
        if isinstance(df.columns, pd.MultiIndex):
            if t not in df.columns.get_level_values(0):
                continue
            d = df[t]
        else:
            d = df
        d = d.dropna(subset=["Close"])
        if not d.empty:
            out[t] = d.tz_convert(NY)
    return out
