#!/bin/zsh
# Run by macOS at login (see ~/Library/LaunchAgents/local.tradingdesk.autostart.plist).
# Waits for the internet, then starts the bot and the desk.
cd "$(dirname "$0")"
for i in {1..60}; do
  curl -s -m 5 -o /dev/null https://fapi.binance.com/fapi/v1/ping && break
  sleep 10
done
echo "$(date '+%Y-%m-%d %H:%M:%S') autostart" >> user_data/logs/autostart.log
./start.sh >> user_data/logs/autostart.log 2>&1
