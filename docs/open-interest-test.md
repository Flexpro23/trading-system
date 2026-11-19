# Open interest test (plan frozen 2026-11-19, before any open-interest number was looked at)

Question: does open interest (how many futures contracts are open) say something about the next 1 to 3 days that price alone does not?

Why it might: a price move with falling open interest is positions being closed, often by force (liquidations); the pressure ends when they are gone. A price move with rising open interest is new money taking the move. The flush-fade test could not tell these apart because it had only price and volume.

## Data

- Binance's public archive (data.binance.vision, daily futures "metrics" files): open interest every 5 minutes for BTC, ETH, SOL, BNB, XRP and DOGE. Downloaded by `backtest/oi_data.py` into `user_data/data/oi/`.
- Open interest is taken in contracts (coins), not in dollars: the dollar value falls when price falls and would only repeat the price move.
- Price: the 1h candles already in `user_data/data/binance/futures`. Funding from the same folder.
- No look-ahead: a signal at the close of a 1h candle uses the last open-interest row stamped at least 5 minutes before that close. A change is not computed across a gap in the archive (either end missing: no signal).

## Signals, checked at each 1h close

Price move = the last 4 hours' return divided by the standard deviation of 4-hour returns over the previous 30 days. Open-interest change = change in contracts over the same 4 hours, ranked against that coin's own 4-hour changes over the previous 90 days.

- **H1. Forced exit, fade it.** Price move at or below -2 and open-interest change in its lowest 5%: long. Mirror (price move at or above +2, open-interest change in the lowest 5%): short.
- **H2. New money, follow it.** Price move at or above +2 and open-interest change in its highest 5%: long. Mirror (price at or below -2, open interest in the highest 5%): short.
- **H3. Filter on the 20-day momentum bot.** Take a momentum entry only when open interest is higher than 3 days before.

## Measurement

- H1 and H2 are measured as events: enter at the next 1h open, close after 24 hours or after 72 hours (two variants each, four in all). No stop. One position per coin and variant; signals during an open position are ignored.
- Result per trade: return minus 0.10% (market order in and out) minus funding paid, plus funding received.
- Signals from several coins in the same hour are one event; the average is taken per event.
- H3 is a freqtrade backtest of TSMomentum with the filter (one variant).

## Controls

- H1, H2: the same price move with no open-interest condition, and the same price move with the open-interest change in the middle (20th to 80th percentile). The signal must beat price alone, not zero.
- H3: the same share of momentum entries dropped at random, five random sequences.
- Long and short shown separately, and each coin separately.

## Judging

- Training period: from the first day all six coins have data to 2025-06-30, split into two halves by date. The period from 2025-07-01 has been read several times for other ideas; it is looked at once, for a variant that passes.
- H1, H2 pass: at least 100 events in each half; average net result per event above 0 in both halves; above the price-alone control in both halves, and by at least 2 standard errors over the whole period; still above 0 at 0.20% cost; positive on at least 4 of 6 coins.
- A variant that passes is then written as a strategy with a stop and must reach the lab's bar (profit factor at least 1.2 in both halves and above the best random sequence) before it becomes a paper profile.
- H3 passes: profit factor above the best of the five random sequences in both windows, and at least 0.10 above plain momentum in both.
- If nothing passes, nothing is added.

## Results (run 2026-11-19)

Data: 11,212 daily files, 137 MB. Bitcoin from 2021-01-01, the other five coins from 2021-12-01 (the archive has nothing earlier), 99.9% of hours covered. Training period 2021-12-01 to 2025-06-30, halves split at 2023-09-16. Raw output: `backtest/out/oi_report.txt` (events) and `lab/oi.tsv` (strategies).

### Stage 1: events, average % per event after 0.10% and funding

| Signal | Hold | Events (halves) | First / second half | Price alone | Difference | Long / short | At 0.20% | Coins positive | Stage 1 |
|---|---|---|---|---|---|---|---|---|---|
| H1 forced exit, fade | 24h | 327 / 346 | -0.10 / +0.58 | -0.03 / -0.13 | +0.33 (1.2 standard errors) | +0.72 / -0.72 | +0.15 | 4 of 6 | Failed |
| H1 forced exit, fade | 72h | 263 / 279 | +0.13 / +0.78 | -0.67 / -0.34 | +0.96 (2.2 standard errors) | +1.05 / -1.34 | +0.36 | 4 of 6 | Passed |
| H2 new money, follow | 24h | 354 / 438 | +0.02 / +0.11 | -0.18 / -0.08 | +0.19 (0.7 standard errors) | +0.51 / -0.98 | -0.03 | 4 of 6 | Failed |
| H2 new money, follow | 72h | 279 / 335 | -0.25 / +0.72 | +0.47 / +0.14 | -0.01 (0.0 standard errors) | +0.96 / -1.32 | +0.18 | 4 of 6 | Failed |

### Stage 2: H1 72h as a strategy (1h candles, stop 6 x ATR(1h), closed after 72 hours, 1% risk)

| Strategy | Trades (halves) | Profit factor, first / second half |
|---|---|---|
| H1 as planned, long and short | 314 / 316 | 0.92 / 1.03 |
| Random entries, same stop and hold (5 sequences) | about 300 each | 0.94 to 1.15 / 0.79 to 1.14 |
| H1 long only (chosen after stage 1, not a planned variant) | 240 / 247 | 1.03 / 1.18 |
| Random long-only entries (5 sequences) | about 295 each | 0.69 to 1.00 / 0.85 to 1.40 |

### H3: momentum with an open-interest filter (2021-12 to 2022, 2023-24)

| Strategy | Trades | Profit factor |
|---|---|---|
| Plain momentum on the same candles | 231 / 411 | 1.14 / 1.57 |
| Entries only when open interest is above 3 days before | 187 / 346 | 1.28 / 1.43 |
| Same share of entries dropped at random (5 sequences) | about 215 / 390 | 1.09 to 1.17 / 1.39 to 1.47 |

- **Nothing passed. Nothing was added to the lab, and 2025-07 onward was not read.**
- **H1 72h is the first signal in this project that beat price alone**, by about 1% per event, in both halves. It did not survive being traded: with a stop, risk-based sizing and a four-trade limit its profit factor was 0.92 / 1.03, inside the range of random entries. Likely reasons: the average gain (+0.46% over three days) is small against three-day swings of several percent, so results depend on a few large trades; signals cluster (66 and 102 were rejected by the trade limit); a stop sized from ATR right after a flush is wide, so position sizes are small exactly on these trades.
- **The gain is on the long side only.** Buying after a fall with open interest dropping made +1.05% per trade; shorting after a rise with open interest dropping lost 1.34%. Long only reached 1.03 / 1.18, below the 1.2 bar, and in the second half random long entries did as well or better (up to 1.40), so that half's result is the rising market.
- **H3** helped in the first window (1.28 against 1.14, above every random sequence) and hurt in the second (1.43 against 1.57, inside the random range). Not consistent.
- Not covered: liquidation feed, order-book depth, the long/short account ratios in the same files (downloaded, not tested).
