"""1-minute Binance USDT-futures candles with taker-buy volume, from Binance's public archive.

Delta of a candle = taker buys - taker sells = 2 * taker_buy_volume - volume. That is exact per minute.
It is not a footprint: where inside the minute, and at which price, the buying happened is not known.

Run:  .venv/bin/python backtest/flow_data.py            (writes user_data/data/flow/<coin>_1m.npz)
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
FLOW = ROOT / "user_data" / "data" / "flow"
RAW = FLOW / "raw"
BASE = "https://data.binance.vision/data/futures/um"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
FIRST, LAST = date(2022, 6, 1), date(2026, 11, 14)
COLS = ["t", "o", "h", "l", "c", "v", "tc", "qv", "n", "tb", "tbq", "x"]


def urls(sym):
    out, d = [], FIRST
    while (d.year, d.month) < (LAST.year, LAST.month):
        out.append(f"{BASE}/monthly/klines/{sym}/1m/{sym}-1m-{d:%Y-%m}.zip")
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    while d <= LAST:
        out.append(f"{BASE}/daily/klines/{sym}/1m/{sym}-1m-{d:%Y-%m-%d}.zip")
        d += timedelta(days=1)
    return out


def fetch(url):
    f = RAW / url.rsplit("/", 1)[1]
    if not f.exists():
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            return url, None
        f.write_bytes(r.content)
    with zipfile.ZipFile(io.BytesIO(f.read_bytes())) as z:
        raw = z.read(z.namelist()[0])
    head = 0 if raw[:9] == b"open_time" else None
    d = pd.read_csv(io.BytesIO(raw), header=head, names=COLS if head is None else None)
    d.columns = COLS
    return url, d[["t", "o", "h", "l", "c", "v", "tb"]]


def build(coin):
    sym = coin + "USDT"
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(fetch, urls(sym)))
    missing = [u.rsplit("/", 1)[1] for u, d in got if d is None]
    d = pd.concat([d for _, d in got if d is not None]).drop_duplicates("t").sort_values("t")
    t = (d.t.values // 1000).astype(np.int64)
    np.savez_compressed(FLOW / f"{coin}_1m.npz", t=t, o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values,
                        v=d.v.values, tb=d.tb.values)
    gaps = int((np.diff(t) != 60).sum())
    print(coin, len(t), "bars", pd.to_datetime(t[0], unit="s"), "to", pd.to_datetime(t[-1], unit="s"),
          "gaps", gaps, "missing files", missing, flush=True)


def load(coin):
    z = np.load(FLOW / f"{coin}_1m.npz")
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for c in sys.argv[1:] or COINS:
        build(c)
