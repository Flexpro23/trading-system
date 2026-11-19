#!/bin/zsh
# One-time setup on a new computer (macOS or Linux). Paper trading only: no exchange key is needed.
cd "$(dirname "$0")"
PY=$(command -v python3.11 || command -v python3.12 || command -v python3)
[ -z "$PY" ] && { echo "Python 3.11 or newer is needed. macOS: brew install python@3.11"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 3.11 or newer is needed (found $("$PY" --version))."; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv || exit 1
.venv/bin/pip install --quiet --upgrade pip
echo "Installing packages (a few minutes)..."
.venv/bin/pip install --quiet -r requirements.txt || { echo "Package install failed."; exit 1; }
mkdir -p user_data/logs user_data/data lab/db lab/pids lab/logs
if [ ! -f user_data/config.json ]; then
  .venv/bin/python - <<'PYEOF'
import json, secrets
c = json.load(open("user_data/config.example.json"))
a = c["api_server"]
a["jwt_secret_key"], a["ws_token"], a["password"] = secrets.token_hex(32), secrets.token_urlsafe(24), secrets.token_urlsafe(12)
json.dump(c, open("user_data/config.json", "w"), indent=2)
PYEOF
  echo "Created user_data/config.json (paper mode, 1,000 USDT simulated wallet)."
fi
if [ ! -f user_data/data/oi/BTC_5m.npz ]; then
  echo "Downloading 130 days of long/short ratio history from data.binance.vision (about 10 MB)..."
  .venv/bin/python -I backtest/oi_data.py --recent 130
fi
echo
echo "Done. Start everything with ./start.sh and open http://127.0.0.1:8090"
