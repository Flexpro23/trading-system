#!/bin/zsh
# usage: ./bt.sh <Strategy> <timerange> [extra freqtrade flags]
# prints: trades | profit factor | expectancy ratio | total % | max drawdown % | long% / short%
cd "$(dirname "$0")"
out=$(.venv/bin/freqtrade backtesting -c user_data/config.json -s "$1" --timerange "$2" --enable-protections --max-open-trades 4 --export none "${@:3}" 2>&1)
echo "$out" | grep " - ERROR - "
g(){ echo "$out" | grep -m1 "$1" | awk -F'│' '{gsub(/^ +| +$/,"",$3); print $3}'; }
printf "%-15s %-18s trades %-5s PF %-5s expect %-14s total %-9s maxDD %-7s L/S %s\n" "$1" "$2" "$(g 'Total/Daily Avg Trades' | cut -d' ' -f1)" "$(g 'Profit factor')" "$(g 'Expectancy (Ratio)')" "$(g 'Total profit %')" "$(g 'Max % of account underwater  ')" "$(g 'Long / Short profit %')"
