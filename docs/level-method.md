# Level method (your trading method, written as rules)

From your BTC daily and 4h TradingView charts and your answers on 2026-11-17.
Status: the levels are built (ui/levels.py) and drawn on the desk chart. Backtests for 5m, 15m and 1h entries are done: no variant held up after fees on the held-back data, and the levels did about as well as a shifted placebo. See docs/level-backtest-results.md.

Version 1 settings:
- Swings: a Fibonacci swing is a turn of 15% on daily, 8% on 4h and 3% on 1h.
- Ranges: the stretches between impulse days. An impulse day moves at least 2× the daily ATR, or two days together move 3×. A range must last at least 20 days.
- Channel: drawn from the lowest low, or the highest high, of the last year to a later swing that hasn't been broken since. The parallel line runs through the furthest point since then. It is ignored when the top is more than 50% above the bottom.
- On the chart: opens show while they have at most 1 touch, and 4h opens only within 4% of the price. The method itself keeps a level until its 3rd touch.

## Levels (computed from Binance USDT futures candles)

1. **Untouched opens**: when one candle's colour flips against the previous candle (green → red or red → green), the new candle's open becomes a level.
   - Computed on monthly, weekly, daily and 4h candles. 1h may be added for scalps.
   - A level stays valid for a few touches and is then removed. The backtest tests 2 and 3 touches.
   - Checked against your chart (Bitstamp BTC): 65,310 D, 62,832 D, 73,915 D (close of the up day), 59,473 W and 58,526 M were all found.
2. **Volume profile per range**: the bot finds sideways stretches, where price held inside a band before breaking out. For each stretch it calculates a fixed-range profile with the point of control (POC), value area high (VAH) and value area low (VAL), using a 70% value area.
   - On your 4h chart these were the ranges Feb–May, Jun–Aug and Sep–Nov. Green is VAH, black is POC, red is VAL.
3. **Fibonacci**: drawn on the last swing of each timeframe (daily, 4h, 1h), at 0.236, 0.382, 0.5, 0.618, 0.66, 0.786 and 0.886.
   - Example on your 4h chart: 87,374 → 62,103.
4. **Channel**: drawn on the daily, along the swing lows with a parallel line through the swing highs. The top and bottom of the channel count as levels.

## Zones

Levels close to each other merge into one zone, and more levels in a zone means a stronger zone. The backtest tests three zone widths: 0.3%, 0.5% and a fraction of ATR 144.

## Entry (scalp first: entries on 5m and 1m)

Price has to test a zone. Four triggers are tested side by side, and the results decide which one is used:
- the first touch of the zone
- the touch plus RSI divergence on the entry timeframe (price makes a lower low while RSI makes a higher low, or the reverse at a top)
- the touch plus a chart pattern: double or triple top or bottom, head and shoulders, or inverse head and shoulders
- the touch plus a candle closing back on the bounce side of the zone

Direction is tested both ways: with no trend filter, and with an EMA 200 trend filter (EMAs 21, 50, 100 and 200 available).

## Exits (all tested)

- **Stop**:
  - beyond the zone plus an ATR buffer
  - beyond the next level behind the zone
  - a fixed percentage
- **Targets 1, 2 and 3**:
  - the next levels ahead
  - Fibonacci 0.382, 0.5 and 0.618 of the swing into the zone
  - 1R, 2R and 3R
- After the first take profit, the stop moves to entry. The desk already does this.

## How it gets judged

- Backtest on Binance futures data for all six coins, fees included.
- The rules are chosen on older data and then checked on held-back recent data before paper trading.
- On 1m and 5m, fees take a large share of each trade: taker fees are 0.05% per side. Entering with a limit order at the level pays the lower maker fee, which needs checking against Binance's current schedule. The backtest reports results after fees.

## Open points

- "Driver" pattern: which pattern is this? If you meant a harmonic pattern such as Cypher or Shark, name it.
- Pattern detection is the least exact part. The patterns are added in version one as you asked, and the backtest will show whether they help.

## Backtest plan (frozen 2026-11-17, before any result was seen)

- Data: Binance USDT futures 5m candles for BTC, ETH, SOL, BNB, XRP and DOGE. Every higher timeframe is built from the 5m candles. Levels warm up from 2021.
- Window: 2022-07-01 to 2026-11-15.
  - Training: 2022-07-01 to 2025-06-30.
  - Held back: 2025-07-01 onward, read once after the selection below.
- No lookahead: levels are rebuilt every hour from candles that had closed by then. An open is born when its flip candle closes, and each touch counts once the touching candle closes.
- Level settings: frozen as listed above (they were set by eye on BTC in November 2026, which falls in the held-back period). They are not retuned after seeing results.
- Variants, every combination of:
  - triggers: first touch, close back out, RSI divergence, pattern
  - stops: zone plus ATR, next level, fixed 0.5%
  - targets: next levels, Fibonacci of the swing, 1R/2R/3R
  - trend filter: none or EMA 200 on 1h
  - zone width: 0.3%, 0.5%, or 0.5× the 1h ATR
  - minimum zone strength: 1, 2 or 3
  - touches traded per level: 2 or 3
- Trade rules:
  - One position per coin at a time.
  - Targets split the position equally. After the first target, the stop moves to entry.
  - A trade is closed after 24 hours.
  - Stops are at least 0.2% from entry.
  - When the stop and a target fall inside the same 5m candle, the stop counts first.
- Fills and fees (Binance standard rates):
  - First-touch entries are limit orders at 0.02% and only fill if price trades 0.02% through the level.
  - Targets are limit orders at 0.02%.
  - Other entries, stops and time exits are market orders at 0.05%; stops pay another 0.02% for slippage.
- Selection rule: the highest average R per trade after fees on the training data, among variants with at least 150 training trades across the six coins and a positive average on at least 4 of the 6 coins.
- Checks on the selected variant:
  - its held-back result
  - a placebo with every level shifted by 0.8%
  - BTC exits replayed on 1m candles
  - the six coins combined as one account: drawdown and the most same-direction positions open at once
- Amendment before any result was seen: a channel counts as broken once the last daily close is outside it. Its lines are then not used as levels until the channel is redrawn from a new swing. This was found while checking the backtest's level map, not from results.

## Swing test plan (frozen 2026-11-17, before any swing result was seen)

- Levels: the same as the scalp test, so the only change is the entry timeframe.
- Entry timeframes: 1h and 15m, each tested on its own, with candles built from 5m.
- Measured in entry-timeframe candles:
  - waiting triggers have 24 candles to fire
  - a trade is closed after 240 candles (10 days on 1h, 2.5 days on 15m)
  - indicators: ATR 144, RSI 14 and 2-candle pivots
- Minimum stop distance: 0.5% on 1h and 0.3% on 15m.
- Fixed stop: 1.5% on 1h and 0.8% on 15m.
- Funding: Binance funding is now charged, on the full position, for every funding time while a trade is open. Longs pay positive rates and shorts receive them.
- Everything else is unchanged from the scalp plan: windows, variants, fees, selection rule and checks.
- Note: the held-back period was already read once, for the 5m test. No settings were tuned on it. Choosing to test swing came from the fee cost measured in training-era results, and swing was in the method from the start.
- Amendment (bug fix, found by the 5m replay check): when one candle touched several zones, the backtest took the deepest zone, which needs knowledge of where the candle would end up. Events are now ordered by the 5m bar of the touch, then by the zone nearest the price. All runs, including the 5m scalp test, were redone after this fix.
