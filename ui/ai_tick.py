"""One check of the AI paper-trading test. Run by the Claude session that watches the desk.

It only talks to the desk on 127.0.0.1:8090, so every trade lands in the desk's paper ledger.
The entry rule is deliberately simple and untested: it exists to exercise the desk, not to make money.
  entry : the coin moved more than 0.15% over the last hour (5-minute closes) -> trade in that direction
  size  : 0.1% of the account at risk, stop 0.5% away, take profits at 1x, 2x and 3x the stop distance
  limits: 2 AI trades per coin, 10 in total
  manage: after take profit 1 the stop moves to entry
Prints the questions waiting for an answer and what it did.
"""
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

import requests

D = "http://127.0.0.1:8090"
H = {"Content-Type": "application/json", "Origin": D}
DB = Path(__file__).resolve().parent.parent / "user_data" / "manual_trades.sqlite"


def post(path, body):
    r = requests.post(f"{D}/api/{path}", headers=H, data=json.dumps(body), timeout=15)
    return r.status_code, r.json()


def answer(qid, text):
    with sqlite3.connect(DB, timeout=10) as c:
        c.execute("update ai set answer=?, answered=strftime('%s','now') where id=?", (text, qid))


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "answer":
        return answer(int(sys.argv[2]), sys.argv[3])
    if len(sys.argv) > 2 and sys.argv[1] == "context":
        with sqlite3.connect(DB) as c:
            return print(c.execute("select context from ai where id=?", (int(sys.argv[2]),)).fetchone()[0])
    s = requests.get(f"{D}/api/state", timeout=15).json()
    mine = [t for t in s["open"] if t.get("by") == "ai"]
    for t in mine:                                           # manage
        past = t["current_rate"] < t["open_rate"] if t["is_short"] else t["current_rate"] > t["open_rate"]
        if t["tps"][0] is None and t["stop_loss_abs"] != t["open_rate"] and past:
            print("stop to entry", t["trade_id"], post("modify", {"trade_id": t["trade_id"], "to_entry": True}))
    if s.get("day", {}).get("locked"):
        print("day locked, no new trades")
    else:
        for pair in s["pairs"]:                              # enter
            if len(mine) >= 10 or sum(t["pair"] == pair for t in mine) >= 2:
                continue
            c = requests.get(f"{D}/api/candles?pair={quote(pair)}&tf=5m", timeout=15).json()
            px, move = s["prices"][pair]["last"], c[-1]["close"] / c[-13]["close"] - 1
            if abs(move) < 0.0015:
                print(f"{pair.split('/')[0]}: hour move {move:+.2%}, no trade")
                continue
            up = move > 0
            lvl = lambda k: round(px * (1 + (k if up else -k) * 0.005), 6)
            code, r = post("trade", {"pair": pair, "side": "long" if up else "short", "sl": lvl(-1),
                                     "tp": lvl(1), "tp2": lvl(2), "tp3": lvl(3), "risk_pct": 0.1, "by": "ai"})
            print(f"{pair.split('/')[0]}: hour move {move:+.2%} ->", code, r)
            if code == 200:
                mine.append({"pair": pair})
    s = requests.get(f"{D}/api/state", timeout=15).json()
    print("\nAI open trades:")
    for t in s["open"]:
        if t.get("by") == "ai":
            print(f"  {t['trade_id']} {t['pair'].split('/')[0]} {'short' if t['is_short'] else 'long'} size {t['notional']:.0f} "
                  f"entry {t['open_rate']:g} now {t['current_rate']:g} stop {t['stop_loss_abs']:g} tps {t['tps']} pnl {t['profit_abs']:+.2f}")
    done = [t for t in s["closed"] if t.get("by") == "ai"]
    print(f"AI closed: {len(done)} lines, total {sum(t['close_profit_abs'] for t in done):+.2f} USDT")
    print(f"account {s['equity']:.2f}, free {s['free']:.2f}, day {s.get('day', {}).get('pnl_pct')}%")
    print("\nQuestions waiting:")
    for q in s["ai"]:
        if not q["answer"]:
            print(f"  #{q['id']} [{q['pair']}] {q['question']}")


main()
