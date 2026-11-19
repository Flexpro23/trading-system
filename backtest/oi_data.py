"""Open interest and long/short ratios for Binance USDT futures, 5-minute rows, from Binance's
public archive (data.binance.vision, daily "metrics" files).

Run:  .venv/bin/python -I backtest/oi_data.py      (writes user_data/data/oi/<coin>_5m.npz)
      add  --recent 130  to fetch only the last 130 days (enough to run the lab; not for backtests)

Columns kept: t (unix seconds), oi (contracts, in coin), oiv (open interest in USDT),
top_ls (top traders' long/short position ratio), ls (all accounts' long/short ratio),
taker (taker buy/sell volume ratio).
"""
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "user_data" / "data" / "oi"
RAW = OUT / "raw"
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
FIRST, LAST = date(2021, 1, 1), date(2026, 11, 17)
KEEP = {"create_time": "t", "sum_open_interest": "oi", "sum_open_interest_value": "oiv",
        "sum_toptrader_long_short_ratio": "top_ls", "count_long_short_ratio": "ls",
        "sum_taker_long_short_vol_ratio": "taker"}


def fetch(url):
    f = RAW / url.rsplit("/", 1)[1]
    if not f.exists():
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=60)
                break
            except requests.RequestException:
                r = None
        if r is None or r.status_code != 200:
            return url, None
        f.write_bytes(r.content)
    try:
        with zipfile.ZipFile(io.BytesIO(f.read_bytes())) as z:
            d = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    except (zipfile.BadZipFile, ValueError):
        f.unlink()
        return url, None
    d = d[list(KEEP)].rename(columns=KEEP)
    d["t"] = pd.to_datetime(d["t"], utc=True).dt.as_unit("s").astype("int64")
    return url, d.apply(pd.to_numeric, errors="coerce")


def build(coin):
    sym = coin + "USDT"
    days = [FIRST + timedelta(days=i) for i in range((LAST - FIRST).days + 1)]
    urls = [f"{BASE}/{sym}/{sym}-metrics-{d:%Y-%m-%d}.zip" for d in days]
    with ThreadPoolExecutor(12) as ex:
        got = list(ex.map(fetch, urls))
    missing = [u.rsplit("-metrics-", 1)[1][:10] for u, d in got if d is None]
    d = pd.concat([d for _, d in got if d is not None]).dropna(subset=["t"]).drop_duplicates("t").sort_values("t")
    d["t"] = (d["t"] // 300) * 300
    d = d.drop_duplicates("t")
    np.savez_compressed(OUT / f"{coin}_5m.npz", **{c: d[c].to_numpy() for c in d.columns})
    first = pd.to_datetime(d.t.iloc[0], unit="s").date()
    print(f"{coin}: {len(d):,} rows from {first}, {len(missing)} days missing"
          + (f" (first {missing[0]}, last {missing[-1]})" if missing else ""), flush=True)


if __name__ == "__main__":
    if "--recent" in sys.argv:                 # new install: only what the lab's crowd profile needs
        days = int(sys.argv[sys.argv.index("--recent") + 1])
        LAST = date.today() - timedelta(days=2)
        FIRST = LAST - timedelta(days=days)
    RAW.mkdir(parents=True, exist_ok=True)
    for c in COINS:
        build(c)
