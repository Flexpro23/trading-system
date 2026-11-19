# Flush fade test (plan frozen 2026-11-19, before any result was seen)

Question: after a sudden, heavy, one-sided move (a liquidation flush), does fading it survive fees?

Why it might: forced sellers do not choose their price. When a cascade of liquidations ends, the selling ends with it. There is no liquidation feed in the data, so the flush is a proxy: a large price move, on heavy volume, with one-sided taker flow.

## Data

Binance USDT futures 1-minute candles with taker-buy volume for BTC, ETH, SOL, BNB, XRP and DOGE (`user_data/data/flow/`), built into 5m and 15m candles.

## Signal (long after a flush down; shorts after a squeeze up are the mirror image)

On a closed candle, with ATR(144) and the median volume of the previous 288 candles both taken from candles before it:

- the candle's close is at least K x ATR below its open,
- its volume is at least 3 times the median,
- taker sells exceed taker buys.

## Trade

- Entry: market order at the open of the next 1-minute candle.
- Stop: 0.25 ATR below the lowest low of the signal candle (and of the wait, if any). Minimum distance 0.2% (5m) or 0.3% (15m).
- Targets: thirds at 1R, 2R and 3R; stop to entry after the first (the desk's scalp engine).
- Closed after 24 hours (5m) or 2.5 days (15m). One position per coin.
- Exits replayed on 1-minute candles. Stop counted first when a candle reaches both; a candle that opens beyond the stop fills at its open.

## Variants: 8

- candles: 5m, 15m
- K: 3, 4
- entry: right after the signal candle, or after the first candle that closes back up within the next 3 candles

## Costs (as docs/order-flow-test.md)

Market orders 0.05%, target limit orders 0.02%, stops another 0.02% slippage, funding while a trade is open. Also reported: the extra entry slippage at which the selected variant breaks even, because spreads are widest in a flush.

## Controls

- **Random times:** same direction mix, stop rule, targets and time limit, entered at random candles; 200 draws of the same number of trades. A variant must beat the 95th percentile of these, not zero.
- **Price move only:** the same K x ATR candle without the volume and taker conditions, to show what they add.
- **Long and short separately**, and the market's own move over each window.
- **Clustering:** a market-wide flush fires several coins at once. Trades whose signals fall in the same hour are one event; the average is taken per event.

## Judging

- Training: 2022-07-01 to 2025-06-30, also shown as two halves.
- Pass: at least 100 events; average R per event after fees above 0 in both halves; above the 95th percentile of the random-time control; still above 0 with costs doubled; positive on at least 4 of 6 coins.
- Selected: the passing variant with the highest average per event. The period from 2025-07-01 has been read several times for other ideas; it is reported once for the selected variant and not used to choose.
- A variant that passes goes to the lab as a paper profile, in-sample only. If none passes, nothing is added.

## Results (run 2026-11-19)

Run by `backtest/flush_bt.py`; raw output in `backtest/out/flush_report.txt`. Training period, six coins, average R per event after fees.

| Variant | Trades | Events | Per event | First / second half | Long / short | Coins positive | Price move only |
|---|---|---|---|---|---|---|---|
| 5m, K 3, right away | 8,982 | 3,743 | -0.30 | -0.26 / -0.35 | -0.26 / -0.39 | 0 of 6 | -0.30 |
| 5m, K 3, after a candle back | 6,880 | 3,447 | -0.19 | -0.16 / -0.22 | -0.19 / -0.25 | 0 of 6 | -0.19 |
| 5m, K 4, right away | 3,975 | 1,851 | -0.28 | -0.25 / -0.33 | -0.19 / -0.42 | 0 of 6 | -0.28 |
| 5m, K 4, after a candle back | 3,180 | 1,715 | -0.19 | -0.17 / -0.21 | -0.14 / -0.24 | 0 of 6 | -0.19 |
| 15m, K 3, right away | 3,198 | 1,622 | -0.22 | -0.24 / -0.20 | -0.16 / -0.31 | 0 of 6 | -0.22 |
| 15m, K 3, after a candle back | 2,528 | 1,444 | -0.19 | -0.15 / -0.24 | -0.12 / -0.23 | 0 of 6 | -0.19 |
| 15m, K 4, right away | 1,454 | 779 | -0.19 | -0.18 / -0.19 | -0.17 / -0.26 | 0 of 6 | -0.19 |
| 15m, K 4, after a candle back | 1,190 | 696 | -0.19 | -0.19 / -0.20 | -0.15 / -0.21 | 1 of 6 | -0.18 |

- **No variant passed.** All eight lose in both halves, long and short. Nothing was selected, so the held-back period was not run.
- **Before fees there is nothing to keep.** Three variants were re-run with fees off (funding still charged): +0.01 R (5m, K 3, after a candle back), +0.01 R (15m, K 3, right away) and -0.09 R (15m, K 4, after a candle back). Fees then take about 0.2 R.
- **Volume and taker flow add nothing.** The same large candle without those two conditions gives the same result to within 0.01 R, and almost the same events: a candle that large nearly always comes with heavy one-sided volume.
- **Random-time entries** with the same stop rule lost more (-0.46 R on 5m, -0.27 R on 15m), because their stops are tighter and fees are a larger share of the risk. They are not a like-for-like comparison for stop distance, and the verdict does not rest on them.
- Not covered: a real liquidation feed, open interest, and order-book depth. This proxy may simply not isolate forced selling.
