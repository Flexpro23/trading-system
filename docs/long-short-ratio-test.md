# Long/short account ratio test (plan frozen 2026-11-19, before any ratio was looked at)

Question: does the positioning of Binance's futures accounts say something about the next 1 to 3 days?

Two readings, every 5 minutes, already in `user_data/data/oi/` (same files as open interest):

- **All accounts:** accounts that are net long divided by accounts that are net short. Mostly small traders.
- **Top traders:** long positions divided by short positions of the largest 20% of accounts by margin.

Why it might work: small accounts tend to buy falls and sell rises and are the ones liquidated; large accounts may be better informed. Why it might not: the ratio moves opposite to price almost mechanically, so it may only repeat the price move.

## Signals, checked at each 1h close

Each reading is the last one stamped at least 5 minutes before the close, ranked against that coin's own hourly readings over the previous 90 days. "High" = highest 5%, "low" = lowest 5%.

- **L1. Fade the crowd.** All-accounts ratio high: short. Low: long.
- **L2. Follow the top traders.** Top-trader ratio high: long. Low: short.
- **L3. Top traders against the crowd.** Top-trader ratio in its highest 20% while all-accounts ratio is in its lowest 20%: long. Mirror: short.

## Measurement

- Enter at the next 1h open, close after 24 hours or 72 hours (L3: 72 hours only). Five variants. No stop. One position per coin and variant.
- Result per trade: return minus 0.10% minus funding paid, plus funding received. Signals in the same hour are one event; averages are per event.

## Controls

- **Random times:** for each coin and half, the same number of longs and of shorts entered at random hours, same hold and costs, 200 draws.
- **Price version (L1):** the crowd is usually long after a fall, so L1 is compared with the same trade taken from price alone: short when the 3-day return is in its lowest 5% of the previous 90 days, long when in its highest 5%.
- Long and short, and each coin, shown separately.

## Judging

- Training period 2021-12-01 to 2025-06-30, halves split at 2023-09-16 (as the open-interest test). The period from 2025-07-01 is looked at once, for a variant that passes.
- Stage 1 pass: at least 100 events in each half; average net result per event above 0 in both halves; average per trade above the 95th percentile of the random-time draws over the whole period; above 0 at 0.20% cost; positive on at least 4 of 6 coins; and for L1, above the price version in both halves.
- Stage 2 (the open-interest test showed stage 1 is not enough): a passer is run as a strategy with a stop of 6 x ATR(1h), 1% risk and the same hold, against five random-entry sequences. It must reach profit factor 1.2 in both halves and beat the best random sequence in both.
- If nothing passes, nothing is added.

## Held-back look: rules written before it (2026-11-19)

Stage 1 and stage 2 results are below. Both L1 variants (24h and 72h) passed both stages, so both are run once on 2025-07-01 to 2026-11-14, with the same stop, risk and trade limit as stage 2, next to the same controls (five random sequences for each hold, the price version, random long-only entries).

- Pass on the held-back period: profit factor above 1 after fees and above the best of the five random sequences with the same hold.
- If both pass, the 24h variant becomes the lab profile (in training it made money long and short in both halves and beat the price version clearly; the 72h variant did not beat the price version in the first half, 1.47 against 1.51, and its second-half profit was all longs). If only one passes, that one. If neither, nothing is added.
- No setting is changed after this look.

## Results (run 2026-11-19)

Coverage: the all-accounts ratio is present for 98.4% of hours from 2021-12-01. The top-trader ratio is missing for most of 2022 (75% coverage), so L2 and L3 have few events in the first half. Raw output: `backtest/reports/ls_report.txt` (events), `lab/ls.tsv` (strategies).

### Stage 1: events, average % after 0.10% and funding

| Signal | Hold | Events (halves) | First / second half | Per trade | Random times, 95th percentile | Long / short | Coins positive | Stage 1 |
|---|---|---|---|---|---|---|---|---|
| L1 fade the crowd | 24h | 839 / 976 | +0.50 / +0.28 | +0.36 | +0.06 | +0.70 / +0.02 | 5 of 6 | Passed |
| L1 fade the crowd | 72h | 415 / 487 | +1.01 / +0.81 | +0.83 | +0.30 | +1.38 / +0.31 | 6 of 6 | Passed |
| L1 price version | 24h | 627 / 733 | +0.12 / +0.23 | +0.10 | +0.06 | +0.49 / -0.32 | 4 of 6 | (control) |
| L1 price version | 72h | 313 / 374 | +0.95 / +0.64 | +0.77 | +0.34 | +1.77 / -0.28 | 5 of 6 | (control) |
| L2 follow top traders | 24h | 368 / 1,277 | -0.40 / +0.01 | -0.09 | +0.07 | +0.24 / -0.53 | 1 of 6 | Failed |
| L2 follow top traders | 72h | 165 / 565 | -1.04 / +0.29 | -0.07 | +0.41 | +0.71 / -1.06 | 2 of 6 | Failed |
| L3 top traders against the crowd | 72h | 139 / 284 | -0.24 / +1.41 | +0.89 | +0.69 | +1.72 / -0.22 | 4 of 6 | Failed |

Timing checks on L1: archive timestamps match Binance's live endpoint (difference 0.0004 at the same stamp, 0.015 one row off); delaying the signal by 1, 2, 4 and 8 hours leaves the result almost unchanged (24h: +0.46 / +0.29 at 8 hours' delay), so it does not depend on the last reading.

### Stage 2: as strategies (1h candles, stop 6 x ATR(1h), 1% risk, four trades at most), profit factor

| Strategy | First half | Second half | Held-back 2025-07 to 2026-11 |
|---|---|---|---|
| L1 24h | 1.43 (674 trades) | 1.32 (802) | 1.15 (650; long -4%, short +26%; drawdown 20%) |
| Random entries, 24h hold (5 sequences) | 0.87 to 1.05 | 0.77 to 1.03 | 0.79 to 0.90 |
| Price version 24h | 1.09 | 1.18 | 1.05 |
| L1 72h | 1.47 (398) | 1.35 (481; long +70%, short -11%) | 1.15 (382) |
| Random entries, 72h hold (5 sequences) | 0.94 to 1.15 | 0.79 to 1.14 | 0.61 to 1.19 |
| Price version 72h | 1.51 | 1.17 | 1.12 |

- **L1 24h passed training and the held-back look** (1.15, above 1 and above every random sequence). It is the first new idea in this project to do so. It became the lab profile `crowdfade` (`LabCrowdFade` in `user_data/strategies/lab.py`), started 2026-11-19.
- **L1 72h failed the held-back look** (1.15 against 1.19 for the best random sequence). Not added.
- **Weak points of the 24h result:** the held-back profit factor is below the 1.2 luck band from the earlier random-entry study and well below training; all held-back profit came from shorts; drawdown was 20% at 1% risk.
- **It is not the momentum bot again:** daily profit correlation with 20-day momentum over the training period is 0.07 (weekly -0.03).
- **Live feed:** Binance's endpoint keeps 30 days, the signal needs 90. The bot keeps its own history in `lab/ratio/` (started from the archive, extended every 4 minutes). Signals built from the endpoint matched the archive on all but 4 of about 1,700 hourly checks; a few archive rows differ from the endpoint by 0.2 to 0.5.
- The held-back period is now used for this idea. No setting may be changed on it.
