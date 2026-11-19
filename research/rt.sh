#!/bin/zsh
# usage: research/rt.sh <label> <Strategy> <timerange> [extra freqtrade flags]
# appends one line to research/results.tsv
cd "$(dirname "$0")/.."
label=$1; strat=$2; range=$3; shift 3
out=$(.venv/bin/freqtrade backtesting -c user_data/config.json -s "$strat" --timerange "$range" --enable-protections --max-open-trades 4 --export none "$@" 2>&1)
g(){ echo "$out" | grep -m1 "$1" | awk -F'│' '{gsub(/^ +| +$/,"",$3); print $3}'; }
row=$(echo "$out" | grep -E "│ +$strat │" | tail -1)
dur=$(echo "$row" | awk -F'│' '{gsub(/^ +| +$/,"",$7); print $7}')
win=$(echo "$row" | awk -F'│' '{n=split($8,a," "); print a[n]}')
err=$(echo "$out" | grep -c " - ERROR - ")
printf "%s\t%s\t%s\ttrades %s\tPF %s\texp %s\ttotal %s\tDD %s\thold %s\twin%% %s\tL/S %s\trejected %s\terr %s\n" "$label" "$strat" "$range" "$(g 'Total/Daily Avg Trades')" "$(g 'Profit factor')" "$(g 'Expectancy (Ratio)')" "$(g 'Total profit %')" "$(g 'Max % of account underwater  ')" "$dur" "$win" "$(g 'Long / Short profit %')" "$(g 'Rejected Entry signals')" "$err" >> "${RESULTS:-research/results.tsv}"
[ "$err" != "0" ] && echo "$out" | grep " - ERROR - " | head -3 >> research/errors.log
exit 0
