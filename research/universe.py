"""Pick the 30 most-traded Binance USDT perpetuals in the 90 days before each test period.
Only currently listed symbols are available from the API; coins delisted since are missing."""
import json, time, requests
from datetime import datetime, timezone
B = "https://fapi.binance.com"
ms = lambda s: int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
PERIODS = {"is1": "2021-04-01", "is2": "2023-01-01", "holdout": "2025-01-01"}
SKIP = {"USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "BTCDOM", "DEFI", "XAU", "XAG", "PAXG"}
info = requests.get(B + "/fapi/v1/exchangeInfo", timeout=20).json()
syms = [s for s in info["symbols"] if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING" and s["baseAsset"] not in SKIP and s["symbol"].isascii()
        and s.get("underlyingType", "COIN") == "COIN"]
print(len(syms), "listed USDT perpetuals considered")
out = {}
for name, start in PERIODS.items():
    t1 = ms(start); t0 = t1 - 90 * 86400000
    vols = {}
    for s in syms:
        if s["onboardDate"] > t1 - 60 * 86400000:      # needs 60 days of history first
            continue
        r = requests.get(B + "/fapi/v1/klines", params={"symbol": s["symbol"], "interval": "1d",
                         "startTime": t0, "endTime": t1 - 1, "limit": 99}, timeout=20)
        if r.status_code != 200:
            continue
        vols[s["symbol"]] = (sum(float(k[7]) for k in r.json()), s["baseAsset"])
        time.sleep(0.03)
    top = sorted(vols.items(), key=lambda kv: -kv[1][0])[:30]
    out[name] = [f"{b}/USDT:USDT" for _, (v, b) in top]
    print(name, len(vols), "eligible ->", " ".join(b for _, (v, b) in top))
json.dump(out, open("research/universe.json", "w"), indent=1)
print("union", len(set(sum(out.values(), []))))
