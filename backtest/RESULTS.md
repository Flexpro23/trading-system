# Backtest results: what was run, what it showed, how to regenerate it

The large result files (`backtest/out/*.pkl`) are not in git and are deleted after the reports are written. This file and `backtest/reports/` are the record. Each group below gives the conclusion, the key numbers, and the exact way to rebuild the raw files.

## Common to all groups

- **Code:** commit `8c3087c` (the backtest code has not changed since the first commit, `82fdb8e`).
- **Coins:** BTC, ETH, SOL, BNB, XRP, DOGE (Binance USDT futures).
- **Windows:** training 2022-07-01 to 2025-06-30, held back 2025-07-01 to 2026-11-14 (`START`, `SPLIT`, `END` in `backtest/level_bt.py`). The end date is fixed in the code, so later data does not change a re-run.
- **Input data** (not in git, in `user_data/data/`, 1.3 GB):
  - `binance/futures/`: `.venv/bin/freqtrade download-data -c user_data/config.json --timerange 20210101- -t 5m 1h 4h 1d` (5m candles and 1h funding rates are what these tests read).
  - `flow/`: `.venv/bin/python backtest/flow_data.py` (1m candles with taker-buy volume, 2022-06-01 to 2026-11-14, no gaps; log in `reports/flow_data.log`).
  - `oi/`: `.venv/bin/python backtest/oi_data.py` (5m open interest and long/short ratios).
- **Checked on 2026-11-22:** Bitcoin was re-run with the code at `8c3087c` for the level test (5m and 1h), the order-flow test (5m) and the limit-entry test (5m). Every variant's trade list was identical to the stored file. All reports were rebuilt from the stored files and matched the saved reports exactly.
- **Run time:** about 3 to 6 minutes per run step on this Mac (six processes); a whole family takes 10 to 20 minutes per entry timeframe.

## 1. Level method on 5m, 15m and 1h entries

- **Raw files:** `out/<COIN>[_15m|_1h][_placebo|_nofees].pkl`, 54 files, 11.0 GB, made 2026-11-17 12:39 to 13:11.
- **Tested:** the user's level method (docs/level-method.md): zone touch with four triggers, three stops, three targets, trend filter, three zone widths, three strengths, two touch limits. 1,296 variants per entry timeframe, each also with levels shifted 0.8% (placebo) and without fees.
- **Result:** failed. Write-up in docs/level-backtest-results.md.

| Entries | Variants positive on training after fees | Passed selection | Selected: training / held back | Real minus shifted levels (train / held) | Best before fees |
|---|---|---|---|---|---|
| 5m | 0 of 1,296 | none | none | +0.0002 R / +0.0086 R | +0.022 R |
| 15m | 0 of 1,296 | none | none | +0.0025 R / +0.0060 R | +0.021 R |
| 1h | 6.6% | 60 | +0.104 R (PF 1.18, 653 trades) / -0.086 R (PF 0.87, 363 trades, positive on 2 of 6 coins) | +0.0009 R / +0.0033 R | +0.201 R |

- **Reports:** `reports/level_report_{5m,15m,1h}.txt`, `reports/level_compare_{5m,15m,1h}.txt`, `reports/report.json` (5m), `reports/report_15m.json`, `reports/report_1h.json`, run log `reports/level_runs.log`.
- **Regenerate:** `KEEP_RAW=1 backtest/run.sh level 5m` (and `15m`, `1h`). That is, per timeframe: `ENTRY=<tf> .venv/bin/python backtest/level_bt.py run`, the same with `--placebo`, the same with `NOFEES=1`, then `report.py "<tag>"` and `compare.py` (tag is empty for 5m, `_15m`, `_1h`).

## 2. Level method, first runs (`out/old/`)

- **Raw files:** `out/old/*.pkl`, 39 files, 7.7 GB, made 2026-11-17 11:13 to 12:37.
- **What they are:** the first 5m, 1h and part of the 15m runs, made before a bug was fixed. When one candle passed through several zones, the code took the deepest zone at the best price. This made 1h look like +0.22 R per trade. Group 1 is the same test redone after the fix.
- **Status:** superseded and wrong. They cannot be regenerated exactly, because the code with the bug was never committed; `reports/superseded/level_bt.scalp.py` is the 5m script from before the swing changes, and `reports/superseded/report_nofees.json` and `report_placebo.json` are reports made from these first runs. Nothing uses them.

## 3. Order flow: zone touch plus delta, market entries

- **Raw files:** `out/flow_<COIN>_<1m|5m|15m>[_placebo|_nofees].pkl`, 54 files, 4.7 GB, made 2026-11-18.
- **Tested:** delta divergence, absorption and delta flip at zones, 192 variants per timeframe (docs/order-flow-test.md).
- **Result:** failed. Positive after fees: 0 of 192 on 1m, 5m and 15m. Best before fees +0.028 R (1m), +0.019 R (5m), +0.032 R (15m); the same variants after fees -0.384 R, -0.183 R, -0.043 R. Delta no better than a plain close back out of the zone; real zones beat shifted zones by 0.000 to 0.006 R.
- **Reports:** `reports/flow_report_{1m,5m,15m}.txt`, run log `reports/flow_runs.log`.
- **Regenerate:** `KEEP_RAW=1 backtest/run.sh flow 5m` (and `1m`, `15m`). That is: `FLOW_ENTRY=<tf> .venv/bin/python backtest/flow_bt.py run`, the same with `--placebo`, the same with `NOFEES=1`, then `flow_bt.py report`.

## 4. Order flow with limit-order entries

- **Raw files:** `out/flowlim_<COIN>_<1m|5m|15m>[_placebo].pkl`, 36 files, 5.2 GB, made 2026-11-19.
- **Tested:** the signals of group 3 entered with a resting limit order at the maker fee, at the close or at the zone, 384 variants per timeframe.
- **Result:** failed. Positive after fees: 0 of 384 on every timeframe, training and held back. Best on training -0.155 R (1m), -0.103 R (5m), -0.045 R (15m). Orders filled 91%, 95%, 97%.
- **Reports:** `reports/flowlim_report_{1m,5m,15m}.txt`, run log `reports/flowlim_runs.log`.
- **Regenerate:** `KEEP_RAW=1 backtest/run.sh flowlim 5m` (and `1m`, `15m`). That is: `FLOW_ENTRY=<tf> .venv/bin/python backtest/flow_limit_bt.py run`, the same with `--placebo`, then `flow_limit_bt.py report`.

## 5. Tests that never had large files

| Test | Result | Report | Regenerate |
|---|---|---|---|
| Flush fade (docs/flush-fade-test.md) | Failed: -0.19 to -0.30 R per event after fees | `reports/flush_report.txt` | `.venv/bin/python backtest/flush_bt.py` |
| Open interest (docs/open-interest-test.md) | Failed as a strategy: PF 0.92 / 1.03 | `reports/oi_report.txt`, `lab/oi.tsv` | `.venv/bin/python -I backtest/oi_bt.py > backtest/reports/oi_report.txt` |
| Long/short ratio (docs/long-short-ratio-test.md) | 24h crowd fade passed: PF 1.43 / 1.32 training, 1.15 held back | `reports/ls_report.txt`, `lab/ls.tsv` | `.venv/bin/python -I backtest/ls_bt.py > backtest/reports/ls_report.txt` |
| Lab and intraday strategies | See README and docs/intraday-test-2.md | `lab/backtests.tsv`, `lab/intraday2.tsv` | `./bt.sh <Strategy> <timerange>` |

## 6. Live scalp rules, with and without a trend filter (2026-11-26)

- **Tested:** the scalp bot's live rules on 15m candles: flip trigger, zones of strength 2+, stop one ATR beyond the zone, signals skipped if the stop is under 0.8% or over 3% away, a third closed at 1R and 2R, stop to entry after 2R, last third as a runner (stop 2R, then 1R behind each new whole R), no time limit, the live overlap rule, fees and funding. Then the same trades split by the swing bot's 20-day momentum rule on closed 4h candles: with the trend, against it, or neutral. Filter and pass rule were fixed in the script's docstring before the first run.
- **Result:** the filter failed on training, so the held-back numbers were not used to decide. The live rules themselves lose money.

| Training 2022-07 to 2025-06 | Width 0.003 | Width ATR |
|---|---|---|
| All trades (the live bot) | 15,904 trades, -0.059 R, PF 0.90, 0 of 6 coins positive | 15,955, -0.051 R, PF 0.91, 0 of 6 |
| With the trend (the filter) | 4,764, -0.102 R, PF 0.84, 0 of 6 | 4,756, -0.086 R, PF 0.86, 0 of 6 |
| Against the trend | 5,107, -0.053 R | 5,170, -0.060 R |
| Neutral | 6,421, -0.029 R | 6,445, -0.022 R |

- **Held back 2025-07 to 2026-11 (printed, not used):** all trades -0.033 R and -0.044 R; with the trend +0.008 R and -0.002 R (about break-even, longs -0.13 R, shorts +0.08 R).
- **Limits:** six coins with 1m taker-buy data, not the 58 the live bot watches; backtest zones, not the desk's exact zones; a stop raised inside a candle that closed beyond it fills at that close.
- **Report:** `reports/scalp_trend_report.txt`, `reports/scalp_trend_report.json`.
- **Regenerate:** `.venv/bin/python backtest/scalp_trend_bt.py` at commit `98ea193` (about 1 minute; no raw files). Data as in "Common to all groups", including `flow/` and the 1h funding rates.

## Rule for new backtests

Reports go to `backtest/reports/` as small text, CSV or JSON files and are committed. Raw trade dumps go to `backtest/out/` only while a test runs; `backtest/run.sh` deletes them after the report unless `KEEP_RAW=1`. A re-run overwrites the earlier one. There is no `old/` folder.
