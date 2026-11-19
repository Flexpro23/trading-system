# Intraday round 2: 15-minute entries, holds of one hour to three days (plan frozen 2026-11-19, before any result)

Question: is there a rule that enters on 15-minute candles, holds between an hour and three days, and beats costs?

Already answered, not re-run (README, docs/): momentum with 5-day to 6-hour lookbacks, breakout, pullback and mean reversion on 1h, the level method, zones plus delta, the VWAP scalp and the flush fade all failed or were flat. So "these" is read as: the lab's ideas moved to a faster clock, plus ideas of a different kind.

## Candidates (7 variants, `user_data/strategies/intraday2.py`)

All use the lab risk model: 1% risk per trade, at most 4 trades, fees and funding charged, stops resolved on 5m candles.

- **A. Pullback inside the 20-day trend.** Only in the direction of the 4h momentum rule that passed. Entry when the 1h RSI(14) has dipped below 40 (above 60 for shorts) and a 15m candle closes back across its 20-candle average. Stop 2 x ATR(1h). Closed after 3 days or when the 4h trend rule exits. A1: no target. A2: target at 2R.
- **B. Breakout inside the 20-day trend.** Same trend condition; entry when a 15m candle closes above the highest high of the previous 24 hours (below the lowest low for shorts). Same stop and exits. B1: no target. B2: target at 2R.
- **C. Relative strength on a fast clock (1h candles).** Rank the six coins by return over the lookback; long the top two, short the bottom two; closed on leaving the top (bottom) three. Stop 3 x ATR(1h). C1: 3-day lookback. C2: 1-day lookback.
- **D. Bitcoin leads.** When Bitcoin's 15m candle moves at least 3 standard deviations (of its last 3 days) and another coin's candle moved less than 1 of its own, enter that coin in Bitcoin's direction. Stop 1 x ATR(1h), target 1.5R, closed after 4 hours.

Screen, no trades: average return by hour of day and day of week, found on the first half and checked on the second (E).

## Controls

- For A and B: random 15m entries taken only inside the same 20-day trend and in its direction, with the same stop and exits, five random sequences. This separates the entry timing from the trend itself.
- For C: random long/short entries on 1h candles with the same stop, closed after 2 days, five sequences.

## Judging

- Windows: 2021-22 and 2023-24. 2025-26 is looked at once, for a variant that passes.
- Pass: at least 200 trades in each window; profit factor above the best of the five random sequences in both windows and at least 1.2 in both; still above 1.05 with fees doubled.
- The earlier intraday round passed at 1.15. Random entries reached 1.16 today, so the bar is higher here.
- A variant that passes becomes a lab profile, in-sample only. If none passes, nothing is added.

## Results (run 2026-11-19)

Raw lines in `lab/intraday2.tsv`. Profit factor after fees, 2021-22 / 2023-24.

| Candidate | Trades | Profit factor | Average hold | Max drawdown | Verdict |
|---|---|---|---|---|---|
| A1 pullback in trend, no target | 697 / 676 | 1.03 / 1.07 | 1.4 days | 34% / 48% | Failed |
| A2 pullback in trend, 2R target | 760 / 748 | 0.95 / 1.02 | 22 hours | 34% / 34% | Failed |
| B1 breakout in trend, no target | 840 / 810 | 1.04 / 1.06 | 1.3 days | 58% / 57% | Failed |
| B2 breakout in trend, 2R target | 1,014 / 976 | 1.08 / 0.97 | 17 hours | 39% / 40% | Failed |
| Control: random entries in the same trend (5 sequences) | about 850 each | 1.01 to 1.21 / 0.97 to 1.08 | 1.4 days | 36-55% / 45-63% | |
| C1 relative strength 1h, 3-day lookback | 1,285 / 1,419 | 1.01 / 1.05 | 1.3 days | 44% / 29% | Failed |
| C2 relative strength 1h, 1-day lookback | 1,714 / 1,939 | 0.99 / 0.95 | 15 hours | 39% / 41% | Failed |
| Control: random 1h entries, 2-day hold (5 sequences) | about 1,040 each | 0.89 to 0.97 / 0.87 to 1.05 | 1.4 days | 33-63% / 46-60% | |
| D Bitcoin leads | 442 / 495 | 0.96 / 0.86 | 2.4 hours | 25% / 36% | Failed |
| E hour of day / weekday | no trades | | | | Failed |

- **No candidate passed.** None reached 1.2 in either window, and none of A or B beat the best random sequence in either window. Nothing was added to the lab; the doubled-fee check and 2025-26 were not run.
- **Entry timing added nothing.** Pullback and breakout entries in a 20-day trend did no better than entering the same trend at random 15m candles. What these trades earn comes from the trend, which the 4h bot already trades at lower cost.
- **A 2-ATR(1h) stop is too tight for the trend.** 26-37% of trades win and drawdowns reach 34-58% at 1% risk, against 5 x ATR(4h) for the 4h bot.
- **C1** was above all five random sequences in 2021-22 and equal to the best in 2023-24, but at 1.01 / 1.05 it is far below the bar and its 2023-24 profit was all longs (+102% against -71% from shorts).
- **E:** the 24 hourly average returns of 2021-22 and 2023-24 correlate at -0.23. The best three hours of the first half returned +0.5, +2.2 and -3.1 hundredths of a percent in the second; a round trip costs 10.
- Not covered: open interest, liquidation feed, order-book depth.
