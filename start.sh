#!/bin/zsh
# Start the paper bot and the trading desk page. Keeps the Mac from idle-sleeping while they run.
cd "$(dirname "$0")"
# A pid file can survive a reboot and point at an unrelated process, so check what the pid is running.
alive(){ [ -f "$1" ] && ps -p "$(cat "$1")" -o command= 2>/dev/null | grep -q "$2"; }
if alive bot.pid freqtrade; then echo "Bot already running (PID $(cat bot.pid))"; else
  nohup caffeinate -i .venv/bin/freqtrade trade -c user_data/config.json -s TSMomentum --logfile user_data/logs/paper.log > /dev/null 2>&1 &
  echo $! > bot.pid
  echo "Bot started (PID $(cat bot.pid)). Log: user_data/logs/paper.log"
fi
if alive ui.pid "ui/server.py"; then echo "Desk already running"; else
  nohup .venv/bin/python ui/server.py > user_data/logs/ui.log 2>&1 &
  echo $! > ui.pid
  echo "Desk: http://127.0.0.1:8090"
fi
./lab.sh start
