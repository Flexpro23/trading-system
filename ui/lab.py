"""Strategy lab: one paper profile per strategy, read side by side.

Profiles are listed in lab/profiles.json. A "freqtrade" profile is its own dry-run bot with its own
database (started by lab.sh); a "ledger" profile is an account in the desk's paper ledger.
Everything here only reads those databases. Results are measured in R: profit divided by the
amount the trade risked at its first stop, so profiles with different sizing can be compared.
"""
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "lab"
UPTIME = LAB / "uptime.json"
HISTORY = LAB / "history.jsonl"
GAP_SECONDS = 300            # the desk missed checks for longer than this: logged as a gap


def profiles() -> list:
    return json.loads((LAB / "profiles.json").read_text())["profiles"]


def _ts(s) -> float:
    if not s:
        return None
    return datetime.fromisoformat(str(s)[:19]).replace(tzinfo=timezone.utc).timestamp()


def _alive(pidfile: Path, word: str) -> bool:
    try:
        pid = pidfile.read_text().strip()
        out = subprocess.run(["ps", "-p", pid, "-o", "command="], capture_output=True, text=True, timeout=5).stdout
        return word in out
    except (OSError, subprocess.SubprocessError):
        return False


def _ft_trades(db: Path, px: dict, since: float = 0.0) -> list:
    """Trades of one freqtrade database, the desk's hand-placed ones left out."""
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("select * from trades order by id").fetchall()
        dist = {r["ft_trade_id"]: float(r["cd_value"]) for r in
                con.execute("select ft_trade_id, cd_value from trade_custom_data where cd_key='stop_dist'")}
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for t in rows:
        if (t["enter_tag"] or "").startswith("manual"):
            continue
        opened = _ts(t["open_date"])
        if opened < since:
            continue
        short = bool(t["is_short"])
        d = dist.get(t["id"]) or abs(t["open_rate"] - (t["stop_loss"] or t["open_rate"]))
        stop = t["open_rate"] + d if short else t["open_rate"] - d
        risk = t["amount"] * d
        is_open = bool(t["is_open"])
        if is_open:
            last = px.get(t["pair"], {}).get("last")
            pnl = None if last is None else (t["open_rate"] - last if short else last - t["open_rate"]) * t["amount"]
        else:
            last, pnl = t["close_rate"], t["close_profit_abs"]
        out.append({"id": t["id"], "pair": t["pair"], "side": "short" if short else "long",
                    "entry": t["open_rate"], "stop": stop, "tps": [], "opened": opened,
                    "closed": None if is_open else _ts(t["close_date"]), "price": last,
                    "reason": t["exit_reason"], "pnl": pnl, "open": is_open,
                    "r": None if pnl is None or risk <= 0 else pnl / risk, "risk": risk})
    return out


def _ledger_trades(db: Path, by: str, px: dict) -> list:
    """Positions of one desk account. Take-profit pieces are folded into the position they came from."""
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("select * from pos where by=? order by id", (by,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    groups = {}
    for r in rows:
        groups.setdefault(r["parent"] or r["id"], []).append(r)
    out = []
    for pid, g in groups.items():
        head = next((r for r in g if r["id"] == pid), None)
        if head is None:
            continue
        kids = sorted((r for r in g if r["id"] != pid), key=lambda r: r["closed"] or 0)
        short = head["side"] == "short"
        stop = kids[0]["sl"] if kids else head["sl"]            # the stop before any move to entry
        qty = sum(r["qty"] for r in g)
        risk = qty * abs(head["entry"] - stop)
        tps = sorted({r["tp"] for r in kids if r["tp"]} | {head[k] for k in ("tp", "tp2", "tp3") if head[k]},
                     reverse=short)
        is_open = head["closed"] is None
        pnl = sum(r["pnl"] or 0 for r in g if r["closed"] is not None)
        last = head["exit"]
        if is_open:
            last = px.get(head["pair"], {}).get("last")
            if last is not None:
                pnl += (head["entry"] - last if short else last - head["entry"]) * head["qty"]
        out.append({"id": f"m{pid}", "pair": head["pair"], "side": head["side"], "entry": head["entry"],
                    "stop": stop, "tps": tps, "opened": head["opened"], "closed": head["closed"], "price": last,
                    "reason": ", ".join(dict.fromkeys(r["reason"] for r in kids + [head] if r["reason"])) or None,
                    "pnl": pnl, "open": is_open, "r": pnl / risk if risk > 0 else None, "risk": risk})
    return out


def _stats(trades: list) -> dict:
    done = [t for t in trades if not t["open"] and t["r"] is not None]
    rs = [t["r"] for t in done]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    gain, loss = sum(t["pnl"] for t in done if t["pnl"] > 0), -sum(t["pnl"] for t in done if t["pnl"] <= 0)
    return {"closed": len(rs), "open": sum(1 for t in trades if t["open"]),
            "win_pct": 100 * len(wins) / len(rs) if rs else None,
            "avg_r": sum(rs) / len(rs) if rs else None, "total_r": sum(rs),
            "avg_win_r": sum(wins) / len(wins) if wins else None,
            "avg_loss_r": sum(losses) / len(losses) if losses else None,
            "pf": gain / loss if loss > 0 else None,
            "pnl": sum(t["pnl"] for t in done),
            "open_pnl": sum(t["pnl"] or 0 for t in trades if t["open"])}


def report(px: dict, control: dict) -> dict:
    out = []
    for p in profiles():
        if p["kind"] == "freqtrade":
            trades = _ft_trades(ROOT / p["db"], px, _ts(p.get("count_from")) or 0.0)
            running = _alive(ROOT / p["pid"], "freqtrade")
        else:
            trades = _ledger_trades(ROOT / p["db"], p["by"], px)
            running = bool(control.get(p["switch"])) if p.get("switch") else True
        trades.sort(key=lambda t: -(t["closed"] or t["opened"] or 0))
        trades.sort(key=lambda t: not t["open"])
        out.append({**p, "running": running, "stats": _stats(trades), "trades": trades[:60]})
    try:
        up = json.loads(UPTIME.read_text())
    except (OSError, ValueError):
        up = {}
    return {"profiles": out, "uptime": up, "now": time.time()}


def heartbeat(px: dict, control: dict) -> None:
    """Called once a minute by the desk. Logs stretches when the desk was not running (Mac asleep or
    desk stopped: the bots miss signals then) and writes one line per profile per UTC day."""
    now = time.time()
    try:
        up = json.loads(UPTIME.read_text())
    except (OSError, ValueError):
        up = {"since": now, "last": now, "gaps": []}
    if now - up["last"] > GAP_SECONDS:
        up["gaps"] = (up["gaps"] + [[up["last"], now]])[-200:]
    up["last"] = now
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    if up.get("day") != day:
        with HISTORY.open("a") as f:
            for p in report(px, control)["profiles"]:
                f.write(json.dumps({"day": day, "id": p["id"], "running": p["running"], **p["stats"]}) + "\n")
        up["day"] = day
    tmp = UPTIME.with_suffix(".tmp")
    tmp.write_text(json.dumps(up))
    os.replace(tmp, UPTIME)
