"""Trade review for the desk: every position the bot, you and the AI test took, grouped and measured.

A position is one entry. Partial closes and take-profit pieces belong to the position they came from,
so win rate and averages count positions, while realized profit counts every closed piece.
"""
from collections import defaultdict
from statistics import median

FEE = 0.0005
TEST_SECONDS = 300           # a desk trade held under 5 minutes at 0.1% risk was a test of the desk
GROUP_SECONDS = 900          # entries from one source within 15 minutes count as opened together
LABEL = {"bot": "Bot strategy", "you": "You", "ai": "AI test", "scalp": "Scalp bot", "test": "Desk tests"}


def _tag_risk(tag: str) -> float:
    for part in (tag or "").split("|"):
        if part.startswith("risk="):
            try:
                return float(part[5:])
            except ValueError:
                pass
    return 0.0


def positions(bot_trades: list, ledger: list, now: float) -> list:
    """One record per position, from freqtrade trades (dicts from its API) and ledger rows (sqlite Rows)."""
    out = []
    for t in bot_trades:
        manual = (t.get("enter_tag") or "").startswith("manual")
        opened = t["open_timestamp"] / 1000
        closed = t["close_timestamp"] / 1000 if not t["is_open"] and t.get("close_timestamp") else None
        pnl = t["close_profit_abs"] if not t["is_open"] else 0.0
        out.append({
            "id": str(t["trade_id"]), "src": "you" if manual else "bot", "pair": t["pair"],
            "side": "short" if t["is_short"] else "long", "lev": t.get("leverage") or 1,
            "risk": _tag_risk(t.get("enter_tag")), "opened": opened, "closed": closed,
            "open": t["is_open"], "realized": pnl or 0.0, "unrealized": t.get("profit_abs") if t["is_open"] else 0.0,
            "fees": (t.get("fee_open_cost") or 0) + (t.get("fee_close_cost") or 0),
            "notional": t["open_rate"] * t["amount"], "entry": t["open_rate"], "exit": t.get("close_rate"),
            "reasons": [t["exit_reason"]] if t.get("exit_reason") else [], "via": "bot",
        })
    groups = defaultdict(list)
    for r in ledger:
        groups[r["parent"] or r["id"]].append(r)
    for pid, rows in groups.items():
        head = next((r for r in rows if r["id"] == pid), None)
        if head is None:
            continue
        rows.sort(key=lambda r: r["closed"] or now)
        done = [r for r in rows if r["closed"] is not None]
        fees = sum(FEE * r["qty"] * (r["entry"] + (r["exit"] or r["entry"])) for r in rows)
        out.append({
            "id": f"m{pid}", "src": head["by"] or "you", "pair": head["pair"], "side": head["side"],
            "lev": head["lev"], "risk": head["risk_pct"], "opened": head["opened"], "closed": head["closed"],
            "open": head["closed"] is None, "realized": sum(r["pnl"] or 0 for r in done), "unrealized": None,
            "fees": fees, "notional": sum(r["qty"] for r in rows) * head["entry"], "entry": head["entry"],
            "exit": head["exit"], "reasons": [r["reason"] for r in done], "via": "desk",
        })
    for p in out:
        held = (p["closed"] or now) - p["opened"]
        p["held"] = held
        if p["src"] == "you" and not p["open"] and held < TEST_SECONDS and p["risk"] <= 0.1:
            p["src"] = "test"
    return sorted(out, key=lambda p: p["opened"], reverse=True)


def _summary(ps: list) -> dict:
    closed = [p for p in ps if not p["open"]]
    wins = [p["realized"] for p in closed if p["realized"] > 0]
    losses = [p["realized"] for p in closed if p["realized"] <= 0]
    reasons = defaultdict(int)
    for p in closed:
        reasons[p["reasons"][-1] if p["reasons"] else "open"] += 1
    by_coin = defaultdict(float)
    for p in ps:
        by_coin[p["pair"].split("/")[0]] += p["realized"]
    return {
        "positions": len(ps), "closed": len(closed), "open": len(ps) - len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "realized": sum(p["realized"] for p in ps),
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": (sum(wins) / -sum(losses)) if wins and sum(losses) < 0 else None,
        "fees": sum(p["fees"] for p in ps),
        "median_hold_min": median(p["held"] for p in closed) / 60 if closed else None,
        "exits": dict(reasons), "by_coin": dict(sorted(by_coin.items(), key=lambda kv: kv[1])),
        "long": sum(p["side"] == "long" for p in ps), "short": sum(p["side"] == "short" for p in ps),
    }


def _clusters(ps: list) -> list:
    """Runs of same-direction entries on several coins within GROUP_SECONDS of each other."""
    out, run = [], []
    for p in sorted(ps, key=lambda p: p["opened"]):
        if run and (p["opened"] - run[0]["opened"] > GROUP_SECONDS or p["side"] != run[0]["side"]):
            if len(run) >= 3:
                out.append(run)
            run = []
        run.append(p)
    if len(run) >= 3:
        out.append(run)
    return out


def observations(src: str, ps: list, s: dict) -> list:
    """Plain facts measured from the trades. No advice on what to trade."""
    notes = []
    closed = [p for p in ps if not p["open"]]
    if src == "bot":
        if not closed:
            first = min((p["opened"] for p in ps), default=None)
            notes.append(f"The strategy has closed no trades yet. It holds {s['open']} open, all "
                         f"{'shorts' if s['short'] == s['positions'] else 'mixed'}, opened together on its 4-hour signal. "
                         "Live paper results cannot judge it until it has closed a few dozen trades; the backtest is the "
                         "only evidence so far." if first else "The strategy has not opened a trade yet.")
        return notes
    if not closed:
        return notes
    stops = [p for p in closed if p["reasons"] and p["reasons"][-1] in ("stop_loss", "liquidation")]
    if stops:
        fast = [p for p in stops if p["held"] < 3600]
        notes.append(f"{len(stops)} of {len(closed)} closed positions ended at the stop; {len(fast)} of those within an hour "
                     f"(median time to stop {median(p['held'] for p in stops) / 60:.0f} min).")
    fees = sum(p["fees"] for p in closed)
    net = sum(p["realized"] for p in closed)
    if fees > 0:
        line = f"Fees on closed positions came to {fees:.2f} USDT; before fees they would have made {net + fees:+.2f} instead of {net:+.2f}."
        if s["avg_loss"] and fees / len(closed) > 0.1 * -s["avg_loss"]:
            line += (f" Each position paid about {fees / len(closed):.2f} in fees, {fees / len(closed) / -s['avg_loss'] * 100:.0f}% "
                     "of the average loss: a tight stop means a large position, and the fee grows with the position.")
        notes.append(line)
    for run in _clusters(ps):
        coins = len({p["pair"] for p in run})
        lost = sum(p["realized"] for p in run)
        notes.append(f"{len(run)} {run[0]['side']}s on {coins} coins were opened within 15 minutes"
                     + (f" (including {len(run) - coins} second entr{'y' if len(run) - coins == 1 else 'ies'} on a coin already held). " if len(run) > coins else ". ")
                     + f"These coins move together, so they "
                     f"acted as one larger trade: {lost:+.2f} USDT.")
    back = [p for p in closed if any(r.startswith("take_profit") for r in p["reasons"])
            and p["reasons"][-1] == "stop_loss" and p["realized"] < 0]
    if back:
        notes.append(f"{len(back)} position(s) took take profit 1 and then the rest hit the original stop, ending at a loss. "
                     "The stop was never moved to entry; that step was done by the 10-minute watch, not by the desk itself. The desk now does it on its own for trades "
                     "that have the option switched on.")
    if s["avg_win"] and s["avg_loss"]:
        need = -s["avg_loss"] / (s["avg_win"] - s["avg_loss"])
        notes.append(f"Average win {s['avg_win']:+.2f}, average loss {s['avg_loss']:+.2f} USDT. At these sizes a win rate "
                     f"above {need * 100:.0f}% breaks even; the actual rate is {s['win_rate'] * 100:.0f}%.")
    return notes


def analyse(bot_trades: list, ledger: list, now: float) -> dict:
    ps = positions(bot_trades, ledger, now)
    groups = {}
    for src in ("bot", "you", "ai", "scalp", "test"):
        sub = [p for p in ps if p["src"] == src]
        if not sub:
            continue
        s = _summary(sub)
        groups[src] = {"label": LABEL[src], **s, "notes": observations(src, sub, s)}
    if "test" in groups:
        groups["test"]["notes"] = [f"Trades of yours held under {TEST_SECONDS // 60} minutes at 0.1% risk, mostly from "
                                   "checking the desk's buttons. Kept apart so they do not count in your results."]
    for p in ps:
        p["label"] = LABEL[p["src"]]
    return {"groups": groups, "positions": ps}
