#!/bin/zsh
# Stop the paper bot and the trading desk page. Open paper positions stay recorded and resume on next start.
cd "$(dirname "$0")"
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then pkill -P "$(cat bot.pid)"; kill "$(cat bot.pid)" 2>/dev/null; echo "Bot stopped"; else echo "Bot not running"; fi
if [ -f ui.pid ] && kill -0 "$(cat ui.pid)" 2>/dev/null; then kill "$(cat ui.pid)"; echo "Desk stopped"; fi
rm -f bot.pid ui.pid
./lab.sh stop
