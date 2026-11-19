#!/usr/bin/env python3
"""Daily health check of the strategy lab. Read-only: prints a short report and appends it to
lab/health.log. Exit code 0 = all running, 1 = something needs attention.
Run: .venv/bin/python lab_health.py"""
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DAY = 86400
now = time.time()
problems, lines = [], []


def alive(pidfile, word):
    try:
        pid = (ROOT / pidfile).read_text().strip()
        out = subprocess.run(["ps", "-p", pid, "-o", "command="], capture_output=True, text=True).stdout
        return word in out
    except OSError:
        return False


def log_state(path):
    """Minutes since the last heartbeat, and error lines in the last 24 hours."""
    try:
        text = (ROOT / path).read_text(errors="replace").splitlines()[-20000:]
    except OSError:
        return None, []
    last, errors = None, []
    for ln in text:
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", ln)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()      # log times are local
        if "Bot heartbeat" in ln:
            last = t
        if " - ERROR - " in ln and now - t < DAY:
            errors.append(ln[:200])
    return (None if last is None else (now - last) / 60), errors


def trades_24h(db, where=""):
    try:
        con = sqlite3.connect(f"file:{ROOT / db}?mode=ro", uri=True, timeout=5)
        cut = datetime.fromtimestamp(now - DAY, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        o = con.execute(f"select count(*) from trades where open_date >= ? {where}", (cut,)).fetchone()[0]
        c = con.execute(f"select count(*) from trades where close_date >= ? {where}", (cut,)).fetchone()[0]
        n = con.execute(f"select count(*) from trades where is_open = 1 {where}").fetchone()[0]
        con.close()
        return o, c, n
    except sqlite3.Error:
        return None


profiles = json.loads((ROOT / "lab/profiles.json").read_text())["profiles"]
for p in profiles:
    if p["kind"] != "freqtrade":
        continue
    up = alive(p["pid"], "freqtrade")
    log = "user_data/logs/paper.log" if p["id"] == "swingbot" else f"lab/logs/{p['id']}.log"
    beat, errors = log_state(log)
    t = trades_24h(p["db"])
    state = "running" if up else "NOT RUNNING"
    if not up:
        problems.append(f"{p['name']} is not running")
    elif beat is None or beat > 15:
        problems.append(f"{p['name']}: no heartbeat for {'?' if beat is None else round(beat)} minutes")
        state = "running, but silent"
    if errors:
        problems.append(f"{p['name']}: {len(errors)} error line(s) in the last 24h, e.g. {errors[-1]}")
    tr = "trades unreadable" if t is None else f"last 24h: {t[0]} opened, {t[1]} closed; {t[2]} open now"
    lines.append(f"- {p['name']}: {state}; {tr}")

try:
    lab = json.loads(urllib.request.urlopen("http://127.0.0.1:8090/api/lab", timeout=10).read())
    lines.append("- Desk page: responding")
    for p in lab["profiles"]:
        if p["kind"] == "ledger":
            s = p["stats"]
            lines.append(f"- {p['name']}: {'on' if p['running'] else 'switched off'}; {s['closed']} closed, {s['open']} open")
except Exception as e:
    problems.append(f"Desk page at 127.0.0.1:8090 is not responding ({type(e).__name__})")

try:
    up = json.loads((ROOT / "lab/uptime.json").read_text())
    gaps = [g for g in up.get("gaps", []) if g[1] > now - DAY]
    missed = sum(g[1] - max(g[0], now - DAY) for g in gaps) / 3600
    lines.append(f"- Missed time in the last 24h (Mac asleep or desk stopped): {missed:.1f} h in {len(gaps)} gap(s)")
    if now - up.get("last", 0) > 600:
        problems.append("The desk's lab heartbeat is more than 10 minutes old")
except (OSError, ValueError):
    problems.append("lab/uptime.json is missing or unreadable")

stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
head = f"Lab health {stamp}: " + ("all running" if not problems else f"{len(problems)} problem(s)")
report = "\n".join([head] + [f"! {x}" for x in problems] + lines)
print(report)
with (ROOT / "lab/health.log").open("a") as f:
    f.write(report + "\n\n")
sys.exit(1 if problems else 0)
