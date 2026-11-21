#!/bin/zsh
# One full test: runs, reports into backtest/reports/ (tracked), then removes the large intermediates.
# usage: backtest/run.sh level 5m|15m|1h
#        backtest/run.sh flow|flowlim 1m|5m|15m
# KEEP_RAW=1 keeps the .pkl files in backtest/out/ (git-ignored, 5 to 11 GB per test family).
cd "$(dirname "$0")" || exit 1
P=../.venv/bin/python; fam=$1; E=$2; R=reports
mkdir -p $R out
case $fam in
  level)
    T=""; [[ $E != 5m ]] && T="_$E"
    ENTRY=$E $P level_bt.py run && ENTRY=$E $P level_bt.py run --placebo && NOFEES=1 ENTRY=$E $P level_bt.py run || exit 1
    ENTRY=$E $P report.py "$T" > $R/level_report_$E.txt && ENTRY=$E $P compare.py > $R/level_compare_$E.txt || exit 1
    raw=(out/{BTC,ETH,SOL,BNB,XRP,DOGE}$T{,_placebo,_nofees}.pkl) ;;
  flow)
    FLOW_ENTRY=$E $P flow_bt.py run && FLOW_ENTRY=$E $P flow_bt.py run --placebo && NOFEES=1 FLOW_ENTRY=$E $P flow_bt.py run || exit 1
    FLOW_ENTRY=$E $P flow_bt.py report > $R/flow_report_$E.txt || exit 1
    raw=(out/flow_{BTC,ETH,SOL,BNB,XRP,DOGE}_$E{,_placebo,_nofees}.pkl) ;;
  flowlim)
    FLOW_ENTRY=$E $P flow_limit_bt.py run && FLOW_ENTRY=$E $P flow_limit_bt.py run --placebo || exit 1
    FLOW_ENTRY=$E $P flow_limit_bt.py report > $R/flowlim_report_$E.txt || exit 1
    raw=(out/flowlim_{BTC,ETH,SOL,BNB,XRP,DOGE}_$E{,_placebo}.pkl) ;;
  *) echo "usage: backtest/run.sh level|flow|flowlim <entry candles>"; exit 1 ;;
esac
[[ -n $KEEP_RAW ]] || rm -f $raw
