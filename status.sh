#!/bin/zsh
# Is the bot running, and what has it traded?
cd "$(dirname "$0")"
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then echo "RUNNING (PID $(cat bot.pid))"; else echo "NOT RUNNING"; fi
grep "Bot heartbeat" user_data/logs/paper.log 2>/dev/null | tail -1 | cut -c1-19 | sed 's/^/Last heartbeat: /'
sqlite3 -header -column user_data/paper_trades.sqlite "select id, pair, case is_short when 1 then 'short' else 'long' end side, round(open_rate,4) entry, round(stop_loss,4) stop, substr(open_date,1,16) opened, case is_open when 1 then 'open' else exit_reason end status, round(close_profit_abs,2) pnl_usdt from trades order by id desc limit 15" 2>/dev/null
