#!/bin/zsh
# Strategy lab: one paper bot per strategy, each with its own 1,000 USDT wallet and database.
# usage: ./lab.sh start | stop | status
# Every profile runs with dry_run forced on by its own file in lab/cfg; there is no live path here.
cd "$(dirname "$0")"
PROFILES=(tsmom:LabTSMomentum dailymom:LabDailyMomentum fundtrend:LabFundingTrend relstrength:LabRelStrength coinflip:LabCoinFlip crowdfade:LabCrowdFade)
alive(){ [ -f "$1" ] && ps -p "$(cat "$1")" -o command= 2>/dev/null | grep -q freqtrade; }
case "$1" in
  start)
    for p in $PROFILES; do
      id=${p%%:*}; strat=${p##*:}
      if alive lab/pids/$id.pid; then echo "$id already running"; continue; fi
      nohup .venv/bin/freqtrade trade -c user_data/config.json -c lab/cfg/$id.json -s $strat \
        --logfile lab/logs/$id.log > /dev/null 2>&1 &
      echo $! > lab/pids/$id.pid
      echo "$id started ($strat, PID $!)"
    done ;;
  stop)
    for p in $PROFILES; do
      id=${p%%:*}
      if alive lab/pids/$id.pid; then kill "$(cat lab/pids/$id.pid)"; echo "$id stopped"; fi
      rm -f lab/pids/$id.pid
    done ;;
  status)
    for p in $PROFILES; do
      id=${p%%:*}
      if alive lab/pids/$id.pid; then s=RUNNING; else s="NOT RUNNING"; fi
      n=$(sqlite3 "file:lab/db/$id.sqlite?mode=ro" "select count(*) || ' trades, ' || coalesce(sum(is_open),0) || ' open' from trades" 2>/dev/null)
      printf "%-12s %-12s %s\n" $id "$s" "$n"
    done ;;
  *) echo "usage: ./lab.sh start | stop | status" ;;
esac
