"""
Trading desk: a small local web app on top of the running freqtrade bot.

- Serves the dashboard page (ui/index.html) on http://127.0.0.1:8090
- Talks to the bot's local API with the login from user_data/config.json (never sent to the page)
- Reads and writes user_data/control.json, the settings the strategy obeys while running
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import analysis
import lab
import levels
import scalp

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "user_data"
CONTROL = USER / "control.json"
STATE = USER / "control_state.json"
PORT = 8090
MAX_STOP_PCT = 50.0
MDB = USER / "manual_trades.sqlite"
MSTATE = USER / "manual_state.json"
FEE = 0.0005                 # taker fee per side
MMR = 0.005                  # maintenance margin rate used for the liquidation price
MIN_NOTIONAL = 5.0           # smallest position the ledger keeps open
SLOTS = ("tp", "tp2", "tp3")
DEFAULTS = {
    "auto_trading": True, "sides": "both", "risk_pct": 1.0, "max_bot_trades": 4,
    "daily_target_pct": 0.0, "daily_loss_pct": 0.0, "on_lock": "close_all", "override_day": "",
    "manual_leverage": 3.0, "target_unit": "pct", "daily_target_usdt": 0.0, "daily_loss_usdt": 0.0,
    "balance_you": 1000.0, "balance_ai": 1000.0,      # starting balance of each desk account (the bot's is dry_run_wallet)
    "balance_scalp": 1000.0,
    "scalp_on": False, "scalp_tf": "15m", "scalp_trigger": "flip", "scalp_risk_pct": 0.25, "scalp_max_trades": 6,
}
DESK_ACCOUNTS = ("you", "ai", "scalp")
SCALP_STOP_AFTER = 200       # closed scalp positions after which a losing scalp bot switches itself off
LIMITS = {"risk_pct": (0.1, 3.0), "max_bot_trades": (0, 100), "manual_leverage": (1.0, 10.0),
          "daily_target_pct": (0.0, 50.0), "daily_loss_pct": (0.0, 50.0),
          "daily_target_usdt": (0.0, 1e7), "daily_loss_usdt": (0.0, 1e7),
          "balance_you": (10.0, 1e7), "balance_ai": (10.0, 1e7), "balance_scalp": (10.0, 1e7),
          "scalp_risk_pct": (0.05, 5.0), "scalp_max_trades": (1, 100)}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def cfg() -> dict:
    return json.loads((USER / "config.json").read_text())


def bot(method: str, path: str, **kw):
    a = cfg()["api_server"]
    url = f"http://{a['listen_ip_address']}:{a['listen_port']}/api/v1/{path}"
    try:
        r = requests.request(method, url, auth=(a["username"], a["password"]), timeout=8, **kw)
    except requests.RequestException:
        raise HTTPException(503, "The bot is not running. Start it with ./start.sh")
    if r.status_code >= 400:
        try:
            msg = r.json()
            msg = msg.get("error") or msg.get("detail") or str(msg)
        except ValueError:
            msg = r.text
        raise HTTPException(400, str(msg))
    return r.json()


def control() -> dict:
    out = dict(DEFAULTS)
    try:
        raw = json.loads(CONTROL.read_text())
        out.update({k: raw[k] for k in DEFAULTS if k in raw})
    except (OSError, ValueError):
        pass
    return out


def write_control(c: dict) -> None:
    tmp = CONTROL.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2))
    os.replace(tmp, CONTROL)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def pairs() -> list:
    return cfg()["exchange"]["pair_whitelist"]


# Coins only the scalp bot trades, to collect signals faster. The swing bot and the lab keep the six in config.json.
SCALP_EXTRA = [f"{c}/USDT:USDT" for c in ("ADA", "AVAX", "LINK", "LTC", "SUI", "TRX", "DOT", "BCH")]


def scalp_pairs() -> list:
    return pairs() + [p for p in SCALP_EXTRA if p not in pairs()]


def symbol(pair: str) -> str:
    return pair.split(":")[0].replace("/", "")


_px = {"t": 0.0, "v": {}, "chg": {}, "chg_t": 0.0, "wait": {}}


def _binance(path: str):
    """A list from Binance, or None. After a rate-limit answer (429/418) nothing is asked until Retry-After has passed."""
    if time.time() < _px["wait"].get(path, 0):
        return None
    try:
        r = requests.get("https://fapi.binance.com" + path, timeout=6)
        if r.status_code in (418, 429):
            _px["wait"][path] = time.time() + float(r.headers.get("Retry-After") or 60)
            print("binance rate limit", path, r.status_code, "waiting", r.headers.get("Retry-After"), flush=True)
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except (requests.RequestException, ValueError):
        return None


def prices() -> dict:
    """Last price every 2 s from the light price endpoint (weight 2); the 24h change once a minute (weight 40).
    Asking the 24h endpoint every 2 s used 1,200 weight a minute and got this address banned for minutes at a time."""
    now = time.time()
    if now - _px["t"] > 2:
        _px["t"] = now                                  # also spaces out retries after a failure
        want = {symbol(p): p for p in scalp_pairs()}
        if now - _px["chg_t"] > 60:
            _px["chg_t"] = now
            try:
                _px["chg"].update({want[d["symbol"]]: float(d["priceChangePercent"])
                                   for d in _binance("/fapi/v1/ticker/24hr") or [] if d.get("symbol") in want})
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        try:
            v = {want[d["symbol"]]: {"last": float(d["price"]), "chg": _px["chg"].get(want[d["symbol"]], 0.0)}
                 for d in _binance("/fapi/v2/ticker/price") or [] if d.get("symbol") in want}
            if v:
                _px["v"] = v
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    return _px["v"]



# ---------------------------------------------------------------- your own trades (paper)
# Trades placed from the desk live in their own ledger, not in the bot. That allows any number
# of positions per coin, in either direction, and keeps them out of the bot's record and pauses.
# Fills are simulated from Binance prices; funding fees are not modelled.
_mlock = threading.Lock()


def mdb():
    c = sqlite3.connect(MDB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def m_init():
    with mdb() as c:
        c.execute("""create table if not exists pos (id integer primary key autoincrement, pair text, side text,
            qty real, entry real, sl real, tp real, risk_pct real, lev real, opened real,
            closed real, exit real, reason text, pnl real)""")
        c.execute("create table if not exists meta (k text primary key, v text)")
        have = {r[1] for r in c.execute("pragma table_info(pos)")}
        c.execute("""create table if not exists ai (id integer primary key autoincrement, asked real, pair text,
            question text, context text, answer text, answered real)""")
        for col, typ in (("tp2", "real"), ("tp3", "real"), ("parent", "integer"), ("by", "text default 'you'"),
                         ("be", "integer default 0"),      # 1 = move the stop to entry after the first take profit
                         ("tf", "text")):                  # candle size of the scalp signal that opened it
            if col not in have:
                c.execute(f"alter table pos add column {col} {typ}")


def m_pnl(r, price: float, qty: float = None) -> float:
    q = r["qty"] if qty is None else qty
    sign = 1 if r["side"] == "long" else -1
    return sign * (price - r["entry"]) * q - FEE * q * (r["entry"] + price)


def liq_price(entry: float, side: str, lev: float) -> float:
    """Isolated margin: the position is closed by the exchange once the loss has eaten the margin
    down to the maintenance level. One flat maintenance rate is used for every coin."""
    d = 1 / lev - MMR
    return entry * (1 - d if side == "long" else 1 + d)


def m_liq(r) -> float:
    return liq_price(r["entry"], r["side"], r["lev"])


def m_open():
    with mdb() as c:
        return c.execute("select * from pos where closed is null order by id").fetchall()


def m_get(pid: int):
    with mdb() as c:
        return c.execute("select * from pos where id=? and closed is null", (pid,)).fetchone()


def m_reduce(pid: int, price: float, reason: str, frac: float = 1.0, when: float = None, slot: str = None) -> bool:
    """Close all or part of a position in one locked step. `slot` is the take profit that fired;
    it must still hold `price`, so a target can never fire twice."""
    when = when or time.time()
    with _mlock, mdb() as c:
        r = c.execute("select * from pos where id=? and closed is null", (pid,)).fetchone()
        if not r or (slot and r[slot] != price):
            return False
        part = r["qty"] * min(1.0, max(0.0, frac))
        if frac >= 0.999 or (r["qty"] - part) * r["entry"] < MIN_NOTIONAL:
            pnl = -r["qty"] * r["entry"] / r["lev"] if reason == "liquidation" else m_pnl(r, price)
            c.execute("update pos set closed=?, exit=?, reason=?, pnl=? where id=?", (when, price, reason, pnl, pid))
            return True
        if part * r["entry"] < 1e-9:
            return False
        c.execute("""insert into pos (pair, side, qty, entry, sl, tp, risk_pct, lev, opened, closed, exit, reason, pnl, parent, by, tf)
                     values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (r["pair"], r["side"], part, r["entry"], r["sl"], price if slot else None, r["risk_pct"], r["lev"],
                   r["opened"], when, price, reason, m_pnl(r, price, part), pid, r["by"], r["tf"]))
        c.execute("update pos set qty=? where id=?", (r["qty"] - part, pid))
        if slot:
            c.execute(f"update pos set {slot}=null where id=?", (pid,))
            behind = r["sl"] < r["entry"] if r["side"] == "long" else r["sl"] > r["entry"]
            if r["be"] and behind:          # a target was taken: the rest can no longer lose more than fees
                c.execute("update pos set sl=? where id=?", (r["entry"], pid))
        return True


def m_close(pid: int, price: float, reason: str, when: float = None) -> bool:
    return m_reduce(pid, price, reason, 1.0, when)


def m_totals(px: dict, by: str = None):
    """Realized profit, open profit and margin in use of the desk's own trades: all of them, or one account's."""
    with mdb() as c:
        realized = c.execute("select coalesce(sum(pnl),0) from pos where closed is not null"
                             + (" and coalesce(by,'you')=?" if by else ""), (by,) if by else ()).fetchone()[0]
    unreal, margin = 0.0, 0.0
    for r in m_open():
        if by and (r["by"] or "you") != by:
            continue
        p = px.get(r["pair"], {}).get("last") or r["entry"]
        unreal += m_pnl(r, p)
        margin += r["qty"] * r["entry"] / r["lev"]
    return realized, unreal, margin


def m_hit(r, lo: float, hi: float):
    """(price, reason, slot) if a price range touches a level of this position.
    The loss side is checked first: the stop, or the liquidation price when that is nearer.
    A range that touches a loss level and a target counts as the loss."""
    up = r["side"] == "long"
    liq = m_liq(r)
    lv, why = (r["sl"], "stop_loss") if ((r["sl"] >= liq) if up else (r["sl"] <= liq)) else (liq, "liquidation")
    if (up and lo <= lv) or (not up and hi >= lv):
        return lv, why, None
    for n, k in enumerate(SLOTS, 1):
        t = r[k]
        if t:
            if (up and hi >= t) or (not up and lo <= t):
                return t, f"take_profit_{n}", k
            break
    return None


def m_apply(pid: int, lo: float, hi: float, when: float = None) -> None:
    """Act on every level the range touches. Each take profit closes an equal share of what is
    left among the targets still waiting; the last one closes the rest."""
    for _ in range(4):
        r = m_get(pid)
        hit = r and m_hit(r, lo, hi)
        if not hit:
            return
        price, why, slot = hit
        left = sum(1 for k in SLOTS if r[k])
        if not m_reduce(pid, price, why, 1.0 / left if slot else 1.0, when, slot) or not slot:
            return


def m_tick():
    rows = m_open()
    px = prices()
    now = time.time()
    with mdb() as c:
        last = c.execute("select v from meta where k='last_check'").fetchone()
    last = float(last[0]) if last else now
    if rows and now - last > 20:
        # The desk was off: walk the 1-minute candles since then so stops and targets still count.
        for pair in {r["pair"] for r in rows}:
            try:
                bars = requests.get("https://fapi.binance.com/fapi/v1/klines", timeout=10, params={
                    "symbol": symbol(pair), "interval": "1m", "startTime": int(last * 1000), "limit": 1500}).json()
            except (requests.RequestException, ValueError):
                continue
            for r in [r for r in rows if r["pair"] == pair]:
                for b in bars:
                    if b[0] / 1000 + 60 <= r["opened"]:
                        continue
                    m_apply(r["id"], float(b[3]), float(b[2]), b[0] / 1000 + 60)
                    if not m_get(r["id"]):
                        break
        rows = m_open()
    lock = None
    try:
        st = json.loads(STATE.read_text())
        if st.get("day") == today() and st.get("locked") and control()["on_lock"] == "close_all":
            lock = st["locked"]
    except (OSError, ValueError):
        pass
    for r in rows:
        p = px.get(r["pair"], {}).get("last")
        if not p:
            continue
        m_apply(r["id"], p, p)
        if lock:
            m_close(r["id"], p, lock)
    realized, unreal, margin = m_totals(px)
    c = control()
    with mdb() as db:                               # the day the separate balances began; the bot counts them from the next
        db.execute("insert or ignore into meta values ('balances_day', ?)", (today(),))   # UTC day, so today's result does not jump
        since = db.execute("select v from meta where k='balances_day'").fetchone()[0]
    base = sum(c["balance_" + k] for k in DESK_ACCOUNTS) if today() > since else 0.0
    tmp = MSTATE.with_suffix(".tmp")                # equity_delta is what the bot adds to its own wallet for the daily limits
    tmp.write_text(json.dumps({"equity_delta": base + realized + unreal, "realized": realized,
                               "unrealized": unreal, "updated": now}))
    os.replace(tmp, MSTATE)
    with mdb() as c:
        c.execute("insert or replace into meta values ('last_check', ?)", (str(now),))


def m_loop():
    while True:
        try:
            m_tick()
        except Exception as e:                    # keep watching stops whatever happens
            print("manual engine:", repr(e), flush=True)
        time.sleep(2)


def m_first_sl(r) -> float:
    """The stop a position had when it opened. A take-profit piece keeps the stop of its moment, and a
    move to entry only follows a take profit, so the first piece holds it. A stop edited by hand before
    any take profit is not recorded anywhere, so then this is the current stop."""
    with mdb() as c:
        k = c.execute("select sl from pos where parent=? order by closed, id limit 1", (r["id"],)).fetchone()
    return k["sl"] if k else r["sl"]


def m_row(r, px: dict) -> dict:
    """A manual position in the same shape the page uses for bot trades."""
    is_open = r["closed"] is None
    p = (px.get(r["pair"], {}).get("last") or r["entry"]) if is_open else r["exit"]
    pnl = m_pnl(r, p) if is_open else r["pnl"]
    nxt = next((r[k] for k in SLOTS if r[k]), None) if is_open else None
    tag = f"manual|sl={r['sl']:g}|risk={r['risk_pct']:g}" + (f"|tp={nxt:g}" if nxt else "")
    return {"trade_id": f"m{r['id']}", "pair": r["pair"], "is_short": r["side"] == "short",
            "open_rate": r["entry"], "current_rate": p, "close_rate": r["exit"], "stop_loss_abs": r["sl"],
            "profit_abs": pnl, "close_profit_abs": r["pnl"], "enter_tag": tag, "exit_reason": r["reason"],
            "profit_ratio": pnl / (r["qty"] * r["entry"] / r["lev"]), "notional": r["qty"] * r["entry"],
            "open_timestamp": r["opened"] * 1000, "close_timestamp": (r["closed"] or 0) * 1000,
            "is_open": is_open, "by": r["by"] or "you", "liq": m_liq(r), "lev": r["lev"], "part": r["parent"] is not None,
            "tps": [r[k] for k in SLOTS] if is_open else [], "be": bool(r["be"]),
            "first_sl": m_first_sl(r) if is_open and r["parent"] is None else None}


def account(px: dict) -> dict:
    """Separate paper accounts: the swing bot's wallet, yours, the AI test's and the scalp bot's. Each has its own starting
    balance, profit and free margin; none can use another's money."""
    c = control()
    wallet = float(cfg().get("dry_run_wallet", 0))
    bot_all, bot_used, bot_open = 0.0, 0.0, 0.0
    try:
        bot_all = float(bot("GET", "profit").get("profit_all_coin") or 0)
        usdt = next((x for x in bot("GET", "balance").get("currencies", []) if x["currency"] == "USDT"), {})
        bot_used = float(usdt.get("used") or 0)
        bot_open = sum(float(t.get("profit_abs") or 0) for t in bot("GET", "status"))
    except HTTPException:
        pass
    acc = {"bot": {"start": wallet, "realized": bot_all - bot_open, "unrealized": bot_open, "used": bot_used}}
    for k in DESK_ACCOUNTS:
        realized, unreal, margin = m_totals(px, k)
        acc[k] = {"start": float(c["balance_" + k]), "realized": realized, "unrealized": unreal, "used": margin}
    for a in acc.values():
        a["equity"] = a["start"] + a["realized"] + a["unrealized"]
        a["free"] = max(0.0, a["equity"] - a["used"])
    mr, mu = sum(acc[k]["realized"] for k in DESK_ACCOUNTS), sum(acc[k]["unrealized"] for k in DESK_ACCOUNTS)
    return {"equity": sum(a["equity"] for a in acc.values()), "free": acc["you"]["free"], "accounts": acc,
            "manual_realized": mr, "manual_unrealized": mu, "bot_all": bot_all}


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Only this page may send commands; another site open in the browser may not."""
    if request.method != "GET":
        origin = request.headers.get("origin")
        ok = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
        if origin not in ok or "application/json" not in request.headers.get("content-type", ""):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/")
def page():
    return FileResponse(Path(__file__).parent / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def state():
    c = control()
    out = {"control": c, "pairs": pairs(), "scalp_pairs": scalp_pairs(), "prices": prices(), "today": today(),
           "bot_up": False, "max_stop_pct": MAX_STOP_PCT, "mmr": MMR}
    try:
        out["day"] = json.loads(STATE.read_text())
    except (OSError, ValueError):
        out["day"] = {}
    if out["day"].get("day") != today():
        out["day"] = {}
    try:
        conf = bot("GET", "show_config")
        out.update(bot_up=True, dry_run=conf.get("dry_run"), strategy=conf.get("strategy"),
                   timeframe=conf.get("timeframe"), bot_state=conf.get("state"),
                   wallet=cfg().get("dry_run_wallet"))
        out["open"] = bot("GET", "status")
        closed = bot("GET", "trades", params={"limit": 100}).get("trades", [])
        out["closed"] = [t for t in closed if not t.get("is_open")][::-1]
        p = bot("GET", "profit")
        out["profit"] = {"closed": p.get("profit_closed_coin"), "all": p.get("profit_all_coin"),
                         "closed_count": p.get("closed_trade_count"),
                         "wins": p.get("winning_trades"), "losses": p.get("losing_trades")}
        out["locks"] = [{"pair": l["pair"], "until": l["lock_end_timestamp"]}
                        for l in bot("GET", "locks").get("locks", []) if l.get("active")]
    except HTTPException as e:
        out["error"] = e.detail
    px = out["prices"]
    acct = account(px)
    out.update(free=acct["free"], equity=acct["equity"], lev=c["manual_leverage"], account=acct)
    out["open"] = out.get("open", []) + [m_row(r, px) for r in m_open()]
    with mdb() as db:
        done = db.execute("select * from pos where closed is not null order by closed desc limit 100").fetchall()
    out["closed"] = sorted(out.get("closed", []) + [m_row(r, px) for r in done],
                           key=lambda t: -(t.get("close_timestamp") or 0))
    out["ai_live"] = ai_ready()
    out["scalp"] = {**_scalp, "pairs": dict(_scalp["pairs"])}
    with mdb() as db:
        out["ai"] = [dict(r) for r in db.execute(
            "select id, asked, pair, question, answer, answered from ai order by id desc limit 12")]
    out.setdefault("profit", {})["all"] = acct["bot_all"] + acct["manual_realized"] + acct["manual_unrealized"]
    return out


@app.get("/api/candles")
def candles(pair: str, tf: str = "15m"):
    if pair not in scalp_pairs() or tf not in ("1m", "5m", "15m", "1h", "4h", "1d"):
        raise HTTPException(400, "Unknown pair or timeframe")
    try:
        rows = requests.get("https://fapi.binance.com/fapi/v1/klines",
                            params={"symbol": symbol(pair), "interval": tf, "limit": 400},
                            timeout=8).json()
        return [{"time": r[0] // 1000, "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4])} for r in rows]
    except (requests.RequestException, ValueError, TypeError, IndexError):
        raise HTTPException(502, "Could not load prices from Binance")


_bars = {}


def bars5m(pair: str, days: int = 60):
    """Last `days` of 5-minute candles from Binance, cached for 15 minutes."""
    hit = _bars.get(pair)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    rows, end = [], None
    for _ in range(days * 288 // 1500 + 1):
        prm = {"symbol": symbol(pair), "interval": "5m", "limit": 1500}
        if end:
            prm["endTime"] = end
        page = requests.get("https://fapi.binance.com/fapi/v1/klines", params=prm, timeout=10).json()
        if not page:
            break
        rows = page + rows
        end = page[0][0] - 1
    a = np.array([[r[0], r[1], r[2], r[3], r[4]] for r in rows[:-1]], dtype=float)   # drop the forming bar
    _bars[pair] = (time.time(), a)
    return a


def replay(a, side: str, stop_pct: float, target_pct: float, horizon: int = 288, n: int = 10000):
    """How often does a plan reach its target before its stop within `horizon` 5-minute bars?
    mc: 10,000 replays, each bar drawn from the coin's own bars at that time of day.
    hist: the same distances started at every bar of the real history.
    A bar that touches both counts as the stop."""
    t, o, h, l, c = a.T
    up = side == "long"
    sl = 1 - stop_pct if up else 1 + stop_pct
    tp = 1 + target_pct if up else 1 - target_pct

    def tally(status, final):
        r = (final - 1) / stop_pct * (1 if up else -1)
        open_r = float(r[status == 0].mean()) if (status == 0).any() else 0.0
        p = [float((status == k).mean()) for k in (1, 2, 0)]
        ev = p[0] * target_pct / stop_pct - p[1] + p[2] * open_r
        return {"target": p[0], "stop": p[1], "neither": p[2], "open_r": open_r, "ev_r": ev}

    def step(price, status, hi, lo):
        hit_s = (lo <= sl) if up else (hi >= sl)
        hit_t = (hi >= tp) if up else (lo <= tp)
        live = status == 0
        status[live & hit_s] = 2
        status[live & ~hit_s & hit_t] = 1

    rh, rl, rc = h[1:] / c[:-1], l[1:] / c[:-1], c[1:] / c[:-1]
    slot = ((t[1:] // 300000) % 288).astype(int)
    by = [np.flatnonzero(slot == k) for k in range(288)]
    pools = [np.concatenate([by[(k + d) % 288] for d in (-2, -1, 0, 1, 2)]) for k in range(288)]
    start = (int(t[-1] // 300000) + 1) % 288
    rng = np.random.default_rng(19)
    price, status = np.ones(n), np.zeros(n, dtype=int)
    for k in range(horizon):
        j = rng.choice(pools[(start + k) % 288], n)
        step(price, status, price * rh[j], price * rl[j])
        price = price * rc[j]
    mc = tally(status, price)

    m = len(c) - horizon - 1
    idx = np.arange(m)
    status, base = np.zeros(m, dtype=int), c[:m]
    for k in range(1, horizon + 1):
        step(None, status, h[idx + k] / base, l[idx + k] / base)
    hist = tally(status, c[idx + horizon] / base)
    hist["n"] = int(m)
    return mc, hist


@app.post("/api/odds")
async def odds(request: Request):
    b = await request.json()
    pair, side = b.get("pair"), b.get("side")
    if pair not in pairs() or side not in ("long", "short"):
        raise HTTPException(400, "Choose a coin and a side")
    px = prices().get(pair, {}).get("last")
    try:
        sl, tp = float(b["sl"]), float(b["tp"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Enter both a stop loss and a take profit to replay the odds")
    up = side == "long"
    if not px or (up and not sl < px < tp) or (not up and not tp < px < sl):
        raise HTTPException(400, "The stop and target must sit on opposite sides of the current price")
    stop_pct, target_pct = abs(px - sl) / px, abs(tp - px) / px
    try:
        a = bars5m(pair)
    except (requests.RequestException, ValueError, TypeError, IndexError):
        raise HTTPException(502, "Could not load price history from Binance")
    if len(a) < 288 * 20:
        raise HTTPException(502, "Not enough price history for this coin")
    mc, hist = replay(a, side, stop_pct, target_pct)
    fee_r = 0.001 / stop_pct                      # 0.05% taker fee each way, as a share of the risk
    return {"mc": mc, "hist": hist, "fee_r": fee_r, "days": round(len(a) / 288),
            "hours": 24, "reward_r": target_pct / stop_pct,
            "drift": float(a[-1][4] / a[0][4] - 1)}


@app.post("/api/control")
async def set_control(request: Request):
    body = await request.json()
    c = control()
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number")
            if not lo <= v <= hi:
                raise HTTPException(400, f"{k} must be between {lo:g} and {hi:g}")
            v = int(v) if k in ("max_bot_trades", "scalp_max_trades") else v
        elif k in ("auto_trading", "scalp_on"):
            v = bool(v)
        elif k == "scalp_tf" and v not in (*scalp.SEC, "1m+5m"):
            raise HTTPException(400, "scalp_tf must be 1m, 5m, 15m or 1m+5m")
        elif k == "scalp_trigger" and v not in scalp.TRIGGERS:
            raise HTTPException(400, "scalp_trigger must be flip, absorb or ddiv")
        elif k == "sides" and v not in ("both", "long", "short"):
            raise HTTPException(400, "sides must be both, long or short")
        elif k == "target_unit" and v not in ("pct", "usdt"):
            raise HTTPException(400, "target_unit must be pct or usdt")
        elif k == "on_lock" and v not in ("close_all", "stop_new"):
            raise HTTPException(400, "on_lock must be close_all or stop_new")
        elif k == "override_day":
            v = today() if v else ""
        c[k] = v
    write_control(c)
    return c


_lv = {}


@app.get("/api/levels")
def level_map(pair: str):
    """Your level method for one coin (ui/levels.py), cached for two minutes."""
    if pair not in scalp_pairs():
        raise HTTPException(400, "Unknown pair")
    hit = _lv.get(pair)
    if hit and time.time() - hit["updated"] < (120 if pair in pairs() else 900):   # scalp-only coins refresh less often
        return hit
    try:
        _lv[pair] = levels.analyse(symbol(pair))
    except (requests.RequestException, ValueError, TypeError, IndexError, KeyError):
        raise HTTPException(502, "Could not load candles from Binance for the levels")
    return _lv[pair]


@app.get("/api/analysis")
def trade_analysis():
    try:
        bot_trades = [t for t in bot("GET", "trades", params={"limit": 500}).get("trades", []) if not t["is_open"]]
        bot_trades += bot("GET", "status")             # open trades with their current profit
        bot_ok = True
    except HTTPException:
        bot_trades, bot_ok = [], False
    with mdb() as c:
        ledger = c.execute("select * from pos").fetchall()
    out = analysis.analyse(bot_trades, ledger, time.time())
    out["bot_up"] = bot_ok
    return out


@app.get("/api/lab")
def lab_report():
    """Strategy lab: every paper profile with its card, results in R and trades (ui/lab.py)."""
    return lab.report(prices(), control())


def lab_loop():
    while True:
        try:
            lab.heartbeat(prices(), control())
        except Exception as e:
            print("lab heartbeat:", repr(e), flush=True)
        time.sleep(60)


@app.post("/api/trade")
async def trade(request: Request):
    b = await request.json()
    pair, side = b.get("pair"), b.get("side")
    if pair not in pairs() or side not in ("long", "short"):
        raise HTTPException(400, "Choose a coin and a side")
    px = prices().get(pair, {}).get("last")
    if not px:
        raise HTTPException(502, "No current price for this coin")
    try:
        sl = float(b["sl"])
        risk = float(b.get("risk_pct") or control()["risk_pct"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "A stop loss price is required")
    tps = read_targets(b)
    up = side == "long"
    if (up and sl >= px) or (not up and sl <= px):
        raise HTTPException(400, f"The stop must be {'below' if up else 'above'} the current price ({px:g})")
    dist = abs(px - sl) / px * 100
    if dist > MAX_STOP_PCT:
        raise HTTPException(400, f"The stop is {dist:.1f}% away; the limit is {MAX_STOP_PCT:g}%")
    if dist < 0.1:
        raise HTTPException(400, "The stop is closer than 0.1% to the price")
    check_targets(tps, up, px)
    lo, hi = LIMITS["risk_pct"]
    if not lo <= risk <= hi:
        raise HTTPException(400, f"Risk must be between {lo:g}% and {hi:g}%")
    day = {}
    try:
        day = json.loads(STATE.read_text())
    except (OSError, ValueError):
        pass
    if day.get("day") == today() and day.get("locked"):
        raise HTTPException(400, "Trading is stopped for today (daily limit reached). Resume it at the top of the page first.")
    lev = float(control()["manual_leverage"])
    liq = liq_price(px, side, lev)
    if (up and sl <= liq) or (not up and sl >= liq):
        raise HTTPException(400, f"At {lev:g}x this trade is liquidated at {liq:g}, before the stop. Use a closer stop or a lower leverage on the Automation page.")
    by = "ai" if b.get("by") == "ai" else "you"
    acct = account(prices())["accounts"][by]            # sized from, and limited by, that account's own balance
    notional = acct["equity"] * risk / 100 / (dist / 100)
    notional = min(notional, acct["free"] * lev)
    if notional < MIN_NOTIONAL:
        raise HTTPException(400, "Not enough free margin in this account for the trade. Close a position or raise the leverage on the Automation page.")
    with _mlock, mdb() as db:
        cur = db.execute("insert into pos (pair, side, qty, entry, sl, tp, tp2, tp3, risk_pct, lev, opened, by, be) values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (pair, side, notional / px, px, sl, tps[0], tps[1], tps[2], risk, lev, time.time(),
                          by, 1 if b.get("be", True) else 0))
        pid = cur.lastrowid
    return {"trade_id": f"m{pid}", "pair": pair, "side": side, "notional": notional, "liq": liq}


def read_targets(b: dict, base=(None, None, None)) -> list:
    """tp, tp2, tp3 from a request. A missing key keeps the current value; an empty one clears it."""
    out = list(base)
    for i, k in enumerate(SLOTS):
        if k in b:
            v = b[k]
            try:
                out[i] = float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                raise HTTPException(400, "A take profit must be a price")
            if out[i] is not None and out[i] <= 0:
                raise HTTPException(400, "A take profit must be a price")
    return out


def check_targets(tps: list, up: bool, px: float) -> None:
    prev = px
    for n, t in enumerate(tps, 1):
        if t is None:
            continue
        if (up and t <= prev) or (not up and t >= prev):
            what = f"the current price ({px:g})" if prev == px else "the take profit before it"
            raise HTTPException(400, f"Take profit {n} must be {'above' if up else 'below'} {what}")
        prev = t


def manual_id(tid):
    if isinstance(tid, str) and tid.startswith("m") and tid[1:].isdigit():
        return int(tid[1:])
    return None


@app.post("/api/modify")
async def modify(request: Request):
    """Move the stop or the take profits of one of your own open trades."""
    b = await request.json()
    pid = manual_id(b.get("trade_id"))
    if pid is None:
        raise HTTPException(400, "Only your own trades can be edited. The bot manages its own stops.")
    r = m_get(pid)
    if not r:
        raise HTTPException(400, "That trade is already closed")
    px = prices().get(r["pair"], {}).get("last")
    if not px:
        raise HTTPException(502, "No current price for this coin")
    up = r["side"] == "long"
    sl = r["sl"]
    if b.get("to_entry"):
        if (up and px <= r["entry"]) or (not up and px >= r["entry"]):
            raise HTTPException(400, "The price is not past the entry yet, so the stop cannot go there")
        sl = r["entry"]
    elif "sl" in b:
        try:
            sl = float(b["sl"])
        except (TypeError, ValueError):
            raise HTTPException(400, "The stop must be a price")
    if sl != r["sl"]:
        if (up and sl >= px) or (not up and sl <= px):
            raise HTTPException(400, f"The stop must be {'below' if up else 'above'} the current price ({px:g})")
        liq = m_liq(r)
        if (up and sl <= liq) or (not up and sl >= liq):
            raise HTTPException(400, f"This trade is liquidated at {liq:g}, so a stop beyond that would never be reached")
    tps = read_targets(b, [r[k] for k in SLOTS])
    if tps != [r[k] for k in SLOTS]:
        check_targets(tps, up, px)
    be = (1 if b["be"] else 0) if "be" in b else r["be"]
    with _mlock, mdb() as db:
        n = db.execute("update pos set sl=?, tp=?, tp2=?, tp3=?, be=? where id=? and closed is null",
                       (sl, tps[0], tps[1], tps[2], be, pid)).rowcount
    if not n:
        raise HTTPException(400, "That trade is already closed")
    return {"trade_id": f"m{pid}", "sl": sl, "tps": tps}


AI_KEY = USER / "ai_key.txt"          # your Claude API key, one line. Never sent to the page.
AI_MODEL = "claude-opus-5-5"
AI_SYSTEM = """You answer questions inside a personal crypto futures trading desk that runs on paper money.
Each question comes with a JSON snapshot: the selected coin, its recent candles on the timeframe the user is viewing,
the user's open paper trades, the account and the desk's rules. Answer from that snapshot: describe what the price
has done, where the user's stops, targets and liquidation prices sit, and what the numbers work out to.
Do not tell the user to buy, sell, hold or close, and do not predict where the price will go; if asked, say the desk's
AI does not give trading advice and offer what the data does show. Say so when the snapshot does not contain what
the question needs. Keep answers short and in plain text with no markdown, since the page shows them as plain text."""


def ai_client():
    """A Claude client if a key is available: user_data/ai_key.txt first, then the usual environment."""
    import anthropic
    try:
        key = AI_KEY.read_text().strip()
    except OSError:
        key = ""
    if key:
        return anthropic.Anthropic(api_key=key)
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return anthropic.Anthropic()
    return None


def ai_ready() -> bool:
    try:
        return ai_client() is not None
    except Exception:
        return False


def ai_answer(qid: int) -> None:
    """Ask Claude and store the answer. Runs in its own thread so the page is not kept waiting."""
    import anthropic
    with mdb() as db:
        row = db.execute("select question, context from ai where id=? and answer is null", (qid,)).fetchone()
    if not row:
        return
    try:
        r = ai_client().beta.messages.create(
            model=AI_MODEL, max_tokens=16000, system=AI_SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": f"Desk snapshot:\n{row['context']}\n\nQuestion: {row['question']}"}])
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        if r.stop_reason == "refusal" or not text:
            text = "The AI declined to answer this question."
    except anthropic.AuthenticationError:
        text = "The API key in user_data/ai_key.txt was rejected. Check the key and ask again."
    except anthropic.PermissionDeniedError:
        text = "The API key is not allowed to use this model."
    except anthropic.RateLimitError:
        text = "The API is rate limited right now. Ask again in a minute."
    except anthropic.APIStatusError as e:
        text = f"The API returned an error ({e.status_code}): {e.message}"
    except anthropic.APIConnectionError:
        text = "Could not reach the API. Check the internet connection and ask again."
    except Exception as e:                        # never leave a question stuck on "waiting"
        text = f"The desk could not ask the AI: {e!r}"
    with _mlock, mdb() as db:
        db.execute("update ai set answer=?, answered=? where id=? and answer is null", (text, time.time(), qid))


@app.post("/api/ask")
async def ask(request: Request):
    """Store a question for the AI with a snapshot of the desk. Nothing here calls a model yet:
    the question waits in the `ai` table until an answer is written to it."""
    b = await request.json()
    q = str(b.get("question") or "").strip()
    pair = b.get("pair")
    if pair not in pairs():
        raise HTTPException(400, "Choose a coin")
    if not 3 <= len(q) <= 1000:
        raise HTTPException(400, "Write a question of up to 1,000 characters")
    px = prices()
    try:
        bars = candles(pair, b.get("tf") if b.get("tf") in ("1m", "5m", "15m", "1h", "4h") else "15m")[-60:]
    except HTTPException:
        bars = []
    ctx = {"pair": pair, "tf": b.get("tf"), "price": px.get(pair), "account": account(px), "control": control(),
           "open": [m_row(r, px) for r in m_open()], "candles": bars}
    with _mlock, mdb() as db:
        if db.execute("select count(*) from ai where answer is null").fetchone()[0] >= 5:
            raise HTTPException(400, "Five questions are already waiting for an answer")
        qid = db.execute("insert into ai (asked, pair, question, context) values (?,?,?,?)",
                         (time.time(), pair, q, json.dumps(ctx))).lastrowid
    live = ai_ready()
    if live:
        threading.Thread(target=ai_answer, args=(qid,), daemon=True).start()
    return {"id": qid, "live": live}


@app.post("/api/close")
async def close(request: Request):
    b = await request.json()
    tid = b.get("trade_id")
    px = prices()
    def close_manual(r, frac=1.0):
        p = px.get(r["pair"], {}).get("last")
        if not p:
            raise HTTPException(502, "No current price for this coin")
        m_reduce(r["id"], p, "force_exit" if frac >= 0.999 else "partial_close", frac)
    pid = manual_id(tid)
    if pid is not None:
        r = m_get(pid)
        if not r:
            raise HTTPException(400, "That trade is already closed")
        try:
            pct = float(b.get("pct", 100))
        except (TypeError, ValueError):
            raise HTTPException(400, "The share to close must be a number")
        if not 1 <= pct <= 100:
            raise HTTPException(400, "The share to close must be between 1% and 100%")
        close_manual(r, pct / 100)
        return {"closed": tid, "pct": pct, "open": m_get(pid) is not None}
    if tid == "all":
        for r in m_open():
            close_manual(r)
        try:
            bot("POST", "forceexit", json={"tradeid": "all"})
        except HTTPException:
            pass
        return {"closed": "all"}
    if not isinstance(tid, int):
        raise HTTPException(400, "Unknown trade")
    return bot("POST", "forceexit", json={"tradeid": tid})


# ---------------------------------------------------------------- scalp bot (paper, its own account)
# Runs by itself inside the desk: on every closed candle of its timeframe it looks, coin by coin, for a touch of
# one of your zones followed by a delta signal (ui/scalp.py), and opens a paper trade in the "scalp" account with
# a stop one ATR beyond the zone and targets at 1, 2 and 3 times the risk. The desk's ledger then manages it
# like any other trade. There is no live path.
_scalp = {"state": "off", "checked": 0.0, "note": "", "pairs": {}}
_seen = {}


def s_enter(pair: str, sig: dict, tf: str, c: dict) -> str:
    px = prices().get(pair, {}).get("last")
    if not px:
        return "no price"
    up, lev = sig["side"] == "long", float(c["manual_leverage"])
    stop = min(sig["stop"], px * (1 - scalp.MIN_STOP[tf])) if up else max(sig["stop"], px * (1 + scalp.MIN_STOP[tf]))
    dist = abs(px - stop) / px
    liq = liq_price(px, sig["side"], lev)
    if dist > 0.03 or ((stop <= liq) if up else (stop >= liq)):
        return f"signal skipped: stop {dist:.2%} away is too wide"
    acct = account(prices())["accounts"]["scalp"]
    notional = min(acct["equity"] * c["scalp_risk_pct"] / 100 / dist, acct["free"] * lev)
    if notional < MIN_NOTIONAL:
        return "signal skipped: no free margin in the scalp account"
    r = abs(px - stop)
    tps = [px + n * r if up else px - n * r for n in (1, 2, 3)]
    with _mlock, mdb() as db:
        db.execute("insert into pos (pair, side, qty, entry, sl, tp, tp2, tp3, risk_pct, lev, opened, by, be, tf) values (?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                   (pair, sig["side"], notional / px, px, stop, tps[0], tps[1], tps[2], c["scalp_risk_pct"], lev, time.time(), "scalp", tf))
    return f"opened {sig['side']} at {px:g}, stop {stop:g} ({sig['why']})"


def s_tick():
    c, now = control(), time.time()
    _scalp["checked"] = now
    if not c["scalp_on"]:
        _scalp["state"] = "off"
        return
    tfs = c["scalp_tf"].split("+")                   # "1m+5m" checks both candle sizes
    mine = [r for r in m_open() if r["by"] == "scalp"]
    px = prices()
    for r in mine:                                   # time exit
        p = px.get(r["pair"], {}).get("last")
        if p and now - r["opened"] > scalp.HOLD[r["tf"] or tfs[-1]]:
            m_close(r["id"], p, "time_exit")
    with mdb() as db:
        n, total = db.execute("select count(*), coalesce(sum(pnl),0) from pos where by='scalp' and closed is not null and parent is null").fetchone()
        total = db.execute("select coalesce(sum(pnl),0) from pos where by='scalp' and closed is not null").fetchone()[0]
    if n >= SCALP_STOP_AFTER and total < 0:
        write_control({**c, "scalp_on": False})
        _scalp.update(state="off", note=f"Switched itself off: {n} closed trades, {total:+.2f} USDT in total.")
        return
    try:
        day = json.loads(STATE.read_text())
    except (OSError, ValueError):
        day = {}
    if day.get("day") == today() and day.get("locked"):
        _scalp["state"] = "paused: daily limit reached"
        return
    _scalp.update(state=f"running on {' and '.join(tfs)} candles", note="")
    n_open = len(mine)
    for tf in tfs:
        n_open = s_scan(tf, c, now, mine, n_open, len(tfs) > 1)


def s_scan(tf: str, c: dict, now: float, mine: list, n_open: int, tag: bool) -> int:
    sec = scalp.SEC[tf]
    bar = int(now // sec) * sec                       # open time of the candle now forming
    for pair in scalp_pairs():
        if _seen.get((pair, tf)) == bar or now < bar + 2:
            continue
        key = f"{pair} {tf}" if tag else pair
        say = lambda m: _scalp["pairs"].__setitem__(key, {"t": now, "msg": m})
        if n_open >= c["scalp_max_trades"]:
            _seen[(pair, tf)] = bar
            say("waiting: the most trades allowed are open")
            continue
        try:
            rows = requests.get("https://fapi.binance.com/fapi/v1/klines", timeout=8,
                                params={"symbol": symbol(pair), "interval": tf, "limit": 500}).json()
            rows = [(k[0] // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[9]))
                    for k in rows if k[0] // 1000 < bar]
            zones = level_map(pair)["zones"]
        except (requests.RequestException, ValueError, TypeError, IndexError, KeyError, HTTPException):
            say("could not load candles or levels; trying again")
            continue                                  # not marked as seen, so the next tick retries
        _seen[(pair, tf)] = bar
        if not rows or rows[-1][0] != bar - sec:
            say("latest candle not available yet")
            continue
        sig = scalp.signal(rows, zones, c["scalp_trigger"], tf)
        last = rows[-1][4]
        if sig and any(r["pair"] == pair and r["side"] == sig["side"] and abs(r["entry"] - last) / last < 0.005 for r in mine):
            say(f"signal skipped: a {sig['side']} from this price area is already open")     # no stacking on one zone
        elif sig:
            msg = s_enter(pair, sig, tf, c)
            if msg.startswith("opened"):
                n_open += 1
            print("scalp:", pair, tf, msg, flush=True)
            say(msg)
        else:
            near = sum(1 for z in zones if z["strength"] >= 2 and abs(z["price"] - rows[-1][4]) / rows[-1][4] < 0.02)
            say(f"no signal; {near} zone{'' if near == 1 else 's'} within 2% of the price")
    return n_open


def s_loop():
    while True:
        try:
            s_tick()
        except Exception as e:
            print("scalp engine:", repr(e), flush=True)
            _scalp["note"] = "Error in the last check: " + repr(e)
        time.sleep(3)


m_init()
threading.Thread(target=m_loop, daemon=True).start()
threading.Thread(target=s_loop, daemon=True).start()
threading.Thread(target=lab_loop, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
