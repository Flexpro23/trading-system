# Order-flow test: zone touch plus delta (plan frozen 2026-11-18, before any result was seen)

Question: does volume delta at your zones give a scalp entry that survives fees, on 1m, 5m and 15m entries?

## Data

- Binance USDT futures 1-minute candles with taker-buy volume, from Binance's public archive, for BTC, ETH, SOL, BNB, XRP and DOGE, June 2022 to 14 November 2026 (`backtest/flow_data.py`).
- Delta of a candle is taker buys minus taker sells. It is exact per minute. CVD is the running sum of delta.
- Not covered: footprint (delta at each price inside a candle) and order-book depth. Footprint needs every single trade, which is about 20 GB per coin for this window; the Mac has 45 GB free.

## Levels

- Base set: the same levels as the earlier test (monthly, weekly, daily and 4h opens, range profiles, Fibonacci, channel), rebuilt every hour from closed candles.
- Plus set: the base set and untouched 1h and 30m opens that lie within 2% of the price.
- A level is traded on its first 3 touches.

## Entries (written for a long at support; shorts are the mirror image)

All three wait for a touch of the zone, enter with a market order at the close of the signal candle, and are cancelled when a candle closes more than one ATR below the zone.

- **Delta divergence:** price makes a lower pivot low at the zone than the pivot low before it (at most 40 candles earlier), while CVD is higher at the new low than at the old one.
- **Absorption:** a candle trades into the zone with heavy net selling (delta at least 1.5 standard deviations below zero, measured over the previous 288 candles) and still closes in the upper half of its own range.
- **Delta flip:** a candle closes back above the zone with heavy net buying (delta at least 1.5 standard deviations above zero).
- Reference, not selectable: a plain close back above the zone with no delta condition. It shows what delta adds.

## Variants: 192 per entry timeframe

- entry: delta divergence, absorption, delta flip
- stop: below the zone by one ATR, or a fixed percentage
- targets: 1R/2R/3R, or the next zones ahead
- trend filter: none, or EMA 200 on 1h
- zone width: 0.3%, or half the 1h ATR
- minimum zone strength: 1 or 2
- level set: base or plus

## Settings per entry timeframe

| | 1m | 5m | 15m |
|---|---|---|---|
| Candles a signal may take after the touch | 60 | 36 | 24 |
| Trade closed after | 4 hours | 24 hours | 2.5 days |
| Minimum stop distance | 0.15% | 0.2% | 0.3% |
| Fixed stop | 0.3% | 0.5% | 0.8% |

ATR 144, 2-candle pivots, one position per coin, equal split over three targets, stop to entry after the first target, stop counted first when a candle reaches both.

## Costs

Market orders 0.05%, target limit orders 0.02%, stops another 0.02% slippage, funding charged while a trade is open.

## Judging

- Training: 2022-07-01 to 2025-06-30. Selection per timeframe: highest average R after fees among variants with at least 150 training trades and a positive average on at least 4 of 6 coins.
- The period from 2025-07-01 was already read three times for the level method. These delta entries have not been run on it, but it is no longer a clean test. It is reported once for the selected variant and not used to choose.
- Checks on a selected variant: the placebo with every level shifted 0.8%, the plain-close reference, and for 5m and 15m a replay of the exits on 1m candles.
- A variant that passes goes to forward paper trading, which is the real test.

## Results (run 2026-11-18)

Run by `backtest/flow_bt.py`; raw reports in `backtest/out/flow_report_{1m,5m,15m}.txt`. Training period, six coins, 192 variants per timeframe.

| Entries | Positive after fees | Passed the selection rule | Best variant before fees | Same variant after fees: training / held back | Typical stop |
|---|---|---|---|---|---|
| 1m | 0 of 192 | none | +0.028 R (absorption) | −0.384 R / −0.422 R | 0.28% |
| 5m | 0 of 192 | none | +0.019 R (delta divergence) | −0.183 R / −0.203 R | 0.63% |
| 15m | 0 of 192 | none | +0.032 R (delta flip) | −0.043 R / −0.087 R | 1.52% |

- **Costs decide it.** Before fees the best variants make 0.02 to 0.03 R per trade. Fees and slippage cost about 0.1 R on 15m, 0.2 R on 5m and 0.3 to 0.4 R on 1m, because a tighter stop means a larger position for the same risk.
- **Delta adds nothing measurable.** Averaged over variants, the plain close back out of the zone did as well before fees as the delta entries on 1m and 5m (1m: +0.011 R against −0.009 to +0.006 R; 5m: +0.003 R against −0.007 to 0.000 R). On 15m, delta divergence was ahead by about 0.02 R (+0.019 R against −0.003 R), which is a fifth of what a 15m trade costs.
- **Zones vs shifted zones.** Real zones beat zones shifted 0.8% by 0.000 to 0.006 R per trade, far below the cost of trading.
- **1h and 30m opens** changed the average by 0.001 to 0.016 R.
- Because nothing passed the selection rule, no variant was selected, so the 1m replay check was not needed. Held-back figures above are shown for the best before-fee variant only and were not used to choose anything.

Not covered: footprint (delta at each price), order-book depth, open interest, and maker (limit) entries for these signals.

# Limit-order entries (plan frozen 2026-11-19, before any result was seen)

Question: do the same signals survive when the entry is a resting limit order at the maker fee instead of a market order?

- Signals, levels, stops, targets, settings, windows, selection rule and costs are unchanged from the plan above. The only change is the entry.
- After the signal candle closes, a limit order is placed and stays for a fixed number of candles: 10 on 1m, 6 on 5m, 4 on 15m. If it is not filled by then, it is cancelled and there is no trade.
- Two places for the order, tested side by side:
  - **At the close:** at the signal candle's closing price.
  - **At the zone:** at the edge of the zone the price just left (the top of a support zone for a long), or at the closing price when that is nearer the zone.
- A limit order counts as filled only when price trades 0.02% through it. It fills at its own price, never better, and pays 0.02%.
- The stop can be hit in the candle that fills the order, and is counted first. Targets count from the next candle.
- Variants: the 192 of the plan above times the two order places, 384 per entry timeframe.
- Reported as well: how many signals were filled, and the placebo with every level shifted 0.8%.
- Known weakness of any limit-entry backtest: the order is filled when price comes back against the trade, so the trades that run away without it are missed. The fill rule above models that; queue position on 1m candles is not modelled and makes real fills somewhat worse than these.

## Results, limit-order entries (run 2026-11-19)

Run by `backtest/flow_limit_bt.py`; raw reports in `backtest/out/flowlim_report_{1m,5m,15m}.txt`. Six coins, 384 variants per timeframe.

| Entries | Positive after fees: training / held back | Passed the selection rule | Best variant on training | Same variant held back | With market entries (best, training) | Orders filled |
|---|---|---|---|---|---|---|
| 1m | 0 of 384 / 0 of 384 | none | −0.155 R (delta flip, order at the close) | −0.165 R | −0.28 R (delta flip average) | 91% |
| 5m | 0 of 384 / 0 of 384 | none | −0.103 R (delta flip, order at the close) | −0.117 R | −0.17 R (delta flip average) | 95% |
| 15m | 0 of 384 / 0 of 384 | none | −0.045 R (delta flip, order at the close) | −0.084 R | −0.043 R (best variant) | 97% |

- **The maker fee does not rescue it.** Averaged over variants, limit entries at the close changed the result per trade by less than 0.01 R on every timeframe (for the delta flip: −0.276 R against −0.281 R on 1m, −0.171 R against −0.168 R on 5m, −0.118 R against −0.098 R on 15m). The fee saved on entry is given back because the order is filled when price comes back against the trade, and the trades that leave at once are missed.
- **An order back at the zone is worse** than one at the close on every timeframe and for nearly every signal. It fills about half as often for the delta flip and catches the trades where the bounce is failing.
- **Real zones vs shifted zones:** a difference of −0.004 to +0.010 R per trade, the same as before.
- No variant passed the selection rule, so none was selected. Held-back figures are shown for the best training variant only and were not used to choose anything.

Not covered: footprint (delta at each price), order-book depth and open interest.
