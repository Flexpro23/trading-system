# Level method backtest: scalp (5m) and swing (15m, 1h) entries

Run on 2026-11-17 by `backtest/level_bt.py`, `report.py`, `compare.py` and `replay.py`, following the frozen plans in docs/level-method.md.
- Coins: BTC, ETH, SOL, BNB, XRP and DOGE.
- Training: 2022-07 to 2025-06. Held back: 2025-07 to 2026-11.
- 1,296 variants per entry timeframe.
- Fees: 0.02% for limit orders, 0.05% for market orders, plus 0.02% slippage on stops. Funding is included for 15m and 1h.

## Bug found and fixed during the swing test

When one candle passed through several zones, the backtest took the deepest zone, at the best price. A real order fills the first zone price reaches. This peeks inside the candle, and it made the first 1h run look strongly profitable, at +0.22 R per trade.

The 5m replay check caught it. Events are now ordered by the 5m candle of the touch, then by the zone nearest the price. All runs below were redone after the fix, and the main engine and the replay now agree trade for trade.

## Result after the fix

| Entries | Variants positive on training (after fees) | Passed the selection rule | Selected variant: training / held back | Real levels vs shifted placebo |
|---|---|---|---|---|
| 5m | 0 of 1,296 | none | — | +0.000 R (train), +0.009 R (held) |
| 15m | 0 of 1,296 | none | — | +0.003 R (train), +0.006 R (held) |
| 1h | 6.6% | 60 variants | +0.10 R / **−0.09 R** (lost on 4 of 6 coins) | +0.001 R (train), +0.003 R (held) |

- **1h:** the variant picked by the frozen rule was RSI divergence, ATR stop, Fibonacci targets, EMA 200 filter, ATR zone width, strength 2 or more, and 2 touches. It lost money on held-back data. About 7% of 1h variants were positive in each period, which is what chance alone produces from 1,296 tries.
- **Before fees:** the best variants made only about +0.02 R per trade on 5m and 15m, and the fees cost far more.
- **Levels vs random nearby prices:** the real levels did slightly better than levels shifted 0.8% in most variants. For example, they were ahead in 65% of 15m variants. The difference is tiny, at 0.001 to 0.009 R per trade, and is 10 to 100 times smaller than the cost of trading.

## What this does and does not show

- **Covered:** the coded version of the method, with these triggers, stops, targets, filters and settings, on six large coins over 4.4 years.
- **Not covered:** your own choice of ranges, swings and patterns by eye, which is not the same as this code. The "driver" pattern is not included.
- **The held-back period has now been read for all three timeframes.** It should not be used again to choose between variants.
