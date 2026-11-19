# Trading system (Binance futures)

> **Paper trading only.** Nothing here can place a real order: the bots run in freqtrade's `dry_run` mode on live Binance prices with a simulated wallet, and no exchange key is used. This is a research project, not financial advice. Backtest and paper results do not predict real results.

## Install on a new computer (macOS or Linux)

Needs Python 3.11 or newer, git, about 1 GB of disk, and an internet connection that can reach Binance.

    git clone <this repository>
    cd <folder>
    ./setup.sh        # creates .venv, installs packages, writes your own local config, downloads 10 MB of ratio history
    ./start.sh        # starts the swing bot, the desk and the six lab bots

Then open http://127.0.0.1:8090 (the Lab page shows each strategy's entries, stops and results). `./status.sh` shows what is running, `./stop.sh` stops everything. The bots only work while the computer is awake.

- Your install is separate: its own paper wallets, databases and settings. Nothing is shared with anyone else's copy.
- `./setup.sh` downloads only what the running bots need. To repeat the backtests you also need price history (`.venv/bin/freqtrade download-data -c user_data/config.json --timerange 20210101- -t 5m 1h 4h 1d`) and the full archives (`backtest/flow_data.py`, `backtest/oi_data.py`), about 1.3 GB.
- The desk's AI chat is optional and needs your own Claude API key in `user_data/ai_key.txt`.
- `lab/profiles.json` holds the lab's start and review dates; change `started` to your own start date.

Engine: [freqtrade](https://www.freqtrade.io) running in **paper mode** (`dry_run: true`).
It uses live Binance futures prices and a simulated 1,000 USDT wallet. No API key is needed and no real order can be sent.

## Files
- `user_data/config.json` - six coins, wallet size, order types, paper/live switch
- `user_data/strategies/candidates.py` - all strategies plus the shared risk model (`RiskBase`)
- `user_data/strategies/TrendPullback.py` - first attempt, retired (lost 72% in backtest)
- `bt.sh` - one-line backtest summary: `./bt.sh TSMomentum 20250101-`
- `user_data/paper_trades.sqlite` - every paper trade (created on first run)

## Paper strategy: TSMomentum (4-hour candles)
Follows the 20-day direction of each coin. Long when price is up more than 5% over 20 days and above its 100-period average; short on the mirror image; exits when either condition flips.

Every trade:
- risks 1% of the account (position size is calculated from the stop distance)
- has a stop loss at 5 x ATR from entry or a 13% price move, whichever is nearer, placed on the exchange in live mode
- has **no fixed take profit**: winners are closed when the trend signal flips (a take profit can be switched on with `REWARD_R`)
- uses at most 3x leverage, isolated margin
- pause rules: 4 stop-outs in 24h stops trading for 12h; 8% drawdown in 48h stops it for 24h

## Commands (run from this folder)
Start, check and stop the paper bot (runs in the background, only while the Mac is awake):

    ./start.sh
    ./status.sh
    ./stop.sh

Show paper results so far:

    .venv/bin/freqtrade show-trades -c user_data/config.json --db-url sqlite:///user_data/paper_trades.sqlite

Refresh history:

    .venv/bin/freqtrade download-data -c user_data/config.json --timeframes 1h 4h 1d --timerange 20210101-

## Backtest results (fees and funding included, 1,000 USDT start)
The six candidates were chosen on 2021-2024 only and the three survivors were then run once on 2025-2026. The retired first strategy (TrendPullback) had already been backtested on 2025-2026, so that period was not completely untouched.

| Strategy | 2021-22 PF | 2023-24 PF | 2025-26 PF (unseen) | 2025-26 return | 2025-26 max drawdown |
|---|---|---|---|---|---|
| Random entries (harness check) | 0.95 | 0.85 | 0.99 | -4% | 28% |
| **TSMomentum (4h)** | 1.34 | 1.57 | **1.44** | +30% | 11% |
| DailyMomentum (1d) | 3.45 | 1.66 | **1.63** | +16% | 6% |
| TurtleDaily (1d) | 2.71 | 1.86 | 1.01 | 0% | 8% |
| DonchianTrend (1h) | 1.07 | 0.98 | not tested | | |
| PullbackRunner (1h) | 1.17 | 0.97 | not tested | | |
| MeanRevert (1h) | 0.77 | 0.72 | not tested | | |

PF = profit factor (gross profit / gross loss; above 1 makes money).

Caveats:
- TSMomentum's unseen-period profit is back-loaded: Jan 2025 - Jun 2026 alone gives PF 1.09 and +5.4%; most of the +30% came after June 2026.
- These results earn TSMomentum a paper test. They do not show it will make money.
- The random-entry row checks that the backtest itself is fair (it should sit near 1.0). It uses a different exit, so it is not a like-for-like control.
- In the 2025-26 run, 4 trades still open at the end account for about a third of the profit (+96 of +295 USDT), and ETH for almost half. Only 5 of 388 trades hit the stop; the rest closed on the signal.
- TurtleDaily and the 1h rows were run before missing funding history for BTC, ETH and SOL (2021-24) was backfilled, so they are slightly flattered.
- Eleven strategy variants were tried in total, so some of the in-sample edge is luck. The unseen-period result is the more honest number.
- TSMomentum's stop was changed (tight trailing -> wide fixed) after an early in-sample run; that was before the unseen period was looked at.
- Most of the long-run profit came from longs in bull markets. Shorts were small or negative in 2023-24.
- Backtests assume market orders fill at the candle open with no slippage.
- A backtest bias was found and fixed along the way: trailing stops must follow closed candles, or the backtest stops trades out unfairly.

## Not done
Telegram alerts, dashboard, always-on hosting, missed-run alert.

## Going live (later, your step)
Only after the paper account has made money over a meaningful period. You create a Binance API key (Futures enabled, withdrawals off, IP whitelist), put it in a separate `user_data/config.live.json` with `"dry_run": false`, and start the bot yourself.

## Trading desk

`./start.sh` starts the bot and the desk; open http://127.0.0.1:8090 (this Mac only, no login).

- **Today**: profit and loss for the UTC day (closed + open trades) against your daily target and loss limit.
- **Ask the AI**: a question box for the selected coin. The question goes out with that coin's candles, your
  open trades and the account. With a Claude API key in `user_data/ai_key.txt` (one line, `chmod 600`, never
  paste it into a chat) Claude answers in a few seconds and each question is billed to that key. Without a key
  the question waits for a Claude Code session running `ui/ai_tick.py`. It gives no buy or sell advice.
- **Place a trade**: pick a coin, long or short, a stop loss, an optional take profit and the risk %.
  The size is worked out from the stop distance. Any number of trades per coin, long and short at once.
  Your trades are kept in the desk's own paper ledger (`user_data/manual_trades.sqlite`), not in the bot:
  the desk checks stops and targets every 2 seconds, and catches up from 1-minute candles after it was off.
  Fees 0.05% per side; funding is not modelled; fills are at the stop/target price with no slippage.
  Size is capped by free margin x the leverage in Rules (1x to 10x). Paper only: there is no live version.
  - **Separate accounts** (Accounts page): the swing bot, you and the AI test each have their own starting balance
    (bot: `dry_run_wallet` in config.json; you and AI: `balance_you`, `balance_ai` in control.json, editable on the page),
    their own profit and their own free margin. A trade is sized from the account that places it.
    Daily limits apply to all accounts together; the desk balances count in them from the UTC day after 2026-11-18.
  - **Take profit 1, 2, 3**: each one closes an equal share of what is left; the last closes the rest.
  - **Stop to entry after a take profit**: on by default for new trades (tick box under the take profits, or in Edit). When a take profit fires, the desk moves the stop of the rest to the entry price.
  - **Analysis and review**: results by Bot strategy, You, AI test and desk tests, with measured notes and every position listed.
  - **Your levels** (buttons above the chart): untouched monthly/weekly/daily opens, other opens, volume ranges (POC/VAH/VAL), Fibonacci, channel, and zones where several levels meet. Rules in docs/level-method.md.
  - **Daily limits in % or USDT**: % is of the balance at 00:00 UTC.
  - **Close**: asks for all of it or a share in percent. A part closed shows as its own line in Closed trades.
    Bot trades can only be closed in full.
  - **Edit**: change the stop or the take profits, or move the stop to entry once the price is past it.
    The stop and take profit lines of your own trades can also be dragged on the chart.
  - **Liquidation**: each position is isolated at the leverage it was opened with. It is liquidated at
    entry -/+ (1/leverage - 0.5%) and loses its whole margin. 0.5% is one flat maintenance rate for every
    coin; Binance's real rates vary by coin and position size. A stop beyond the liquidation price is refused.
- **Replay the odds** (needs a stop and a target): how often that stop and target were reached, over 24 hours,
  when started at every 5-minute candle of the coin's last ~60 days, plus 10,000 shuffled replays of the next
  24 hours and the average result after fees. It describes random moments, not the current one, and it carries
  the last 60 days' trend. Idea borrowed from the "Time Machine" in Jakeschincariol/day-trader-skill.
- **Rules** (saved to `user_data/control.json`, obeyed by the running bot within seconds):
  daily profit stop, daily loss stop, what happens when one is reached, risk per trade,
  how many trades the bot may hold, which direction it may trade, and a switch that stops the bot opening trades.
- **Closed trades** are split into Bot and You, so the bot's paper record stays readable.

Files: `ui/server.py` (local server, holds the bot login), `ui/index.html` (the page),
rules and manual-trade handling in `RiskBase` in `user_data/strategies/candidates.py`.
Backtests ignore `control.json`; `bt.sh` results are unchanged by it.

Your trades no longer touch the bot's record or its safety pauses. They do count in the account total and in
today's profit and loss, so the daily stops cover both. (Trades placed by hand before 2026-11-16 19:40 went
through the bot and remain in its database.)

Not exercised in testing: the daily stop in "close every trade" mode, the loss-limit stop, "Close all",
and a manual stop loss being hit by the market.

The daily target is a stop rule: it ends trading for the day once reached. It cannot make a day reach it.

freqtrade's own UI is still at http://127.0.0.1:8080 (login under `api_server` in `user_data/config.json`).

Before going live, also: the desk at :8090 has no login and can place and close trades, and
`force_entry_enable` is on. The desk reads `user_data/config.json`; it would need pointing at the live config.

## Scalp bot (2026-11-18)

Automation page, off by default. `ui/scalp.py` holds the entry rule and `s_tick` in `ui/server.py` runs it inside the desk,
with no Claude session needed. On each closed 1m, 5m or 15m candle it checks every coin for a touch of one of your zones
(strength 2 or more, as drawn on the chart) followed by a delta signal: delta flip, absorption or delta divergence.
It opens a paper trade in its own `scalp` account: stop one ATR beyond the zone, targets at 1R, 2R and 3R, stop to entry
after the first target, several trades per coin allowed (not two in the same direction within 0.5% of each other), closed after 4 hours (1m), 24 hours (5m) or 2.5 days (15m).
It switches itself off after 200 closed trades if the total is negative. Settings are the `scalp_*` keys in control.json.
In the backtest (docs/order-flow-test.md) none of these signals made money after fees on 5m or 15m. Paper only.

## Intraday research (2026-11-16)

Question: can the bot trade faster ("in and out") and still beat costs? Fixed set of candidates in
`user_data/strategies/intraday.py`, no parameter tuning. Raw output: `research/results.tsv`
(`research/rt.sh <label> <Strategy> <timerange> [flags]`). Fees, funding and the bot's safety pauses
included; finer candles used to resolve stops inside each candle.

Pass rule set beforehand: profit factor >= 1.15 in both 2021-22 and 2023-24, at least 200 trades in each,
>= 1.05 with fees doubled, better than random entries; then one look at 2025-26, split at June 2026.

| Candidate | Avg hold | 2021-22 PF | 2023-24 PF | Result |
|---|---|---|---|---|
| Momentum, 5-day lookback (1h) | ~2 days | 1.28 | 1.25 | Passed; see below |
| Momentum, 2-day lookback (1h) | ~22 hours | 1.02 | 1.04 | Failed (break-even) |
| Momentum, 1-day lookback (1h) | ~12 hours | 0.81 | 0.89 | Failed |
| Momentum, 6-hour lookback (15m) | ~3 hours | 0.92 | 0.54 | Failed |
| VWAP + EMA(8) + RSI(3) scalp, as written | - | - | - | Never triggers: its entry conditions cannot occur on one candle |
| Same scalp, workable reading (5m, 0.3% stop, BTC) | ~17 min | 0.40 | 0.21 | Failed; 0.97 with zero fees |
| Random entries (1h) | | | 0.94 | Harness check |

5-day momentum on 2025-26: Jan 2025 - Jun 2026 PF 1.02 (+4%), 0.93 with doubled fees; Jun 2026 - now PF 1.30 (+20%).
Drawdown 18-26%. Doubled fees in-sample: 1.21 and 1.12. It is flat for 17 months of the unseen period and
is no better than TSMomentum, which trades less and draws down less. Not promoted.

Why speed loses: a round trip costs about 0.10% of the position in fees. As a share of the amount risked
that is 2% with a 5% stop, 10% with a 1% stop, 20% with a 0.5% stop and 33% with a 0.3% stop, before slippage.

Counting earlier work, about 18 strategy variants have now been tried on this data.

## More coins and more slots (2026-11-16)

Question: does the paper bot's rule take more trades, without losing quality, on 30 coins instead of 6?
Coins: the 30 most-traded Binance USDT perpetuals in the 90 days before each period (`research/universe.py`,
`research/universe.json`); coins delisted since are missing. 10,000 USDT test wallet so small positions are not
skipped. Raw output: `research/wide.tsv`. Profit factor, with trades per day in brackets:

| Setup | 2021-22 | 2023-24 | Jan 2025 - Jun 2026 | Jun 2026 - now |
|---|---|---|---|---|
| 6 coins, 4 slots, 1% risk (the paper bot) | 1.46 (0.58) | 1.56 (0.56) | 1.06 (0.62) | 3.12 (0.46) |
| 30 coins, 4 slots, 1% risk | 1.22 (0.60) | 1.42 (0.56) | 1.00 (0.61) | 2.05 (0.53) |
| 30 coins, 8 slots, 0.5% risk | 1.29 (1.08) | 1.14 (1.13) | 0.92 (1.14) | 2.18 (0.93) |

More coins alone adds no trades (the 4 slots are already full). More slots roughly doubles the trade count,
to about one a day, and the profit factor is lower in all four periods. Not adopted.

## Auto-start

macOS runs `autostart.sh` when you log in (`~/Library/LaunchAgents/local.tradingdesk.autostart.plist`).
It waits for the internet (up to 10 minutes), then runs `./start.sh`. Log: `user_data/logs/autostart.log`.
It runs at login, not at power-on: after a restart nothing starts until you log in.
It does not restart the bot if it crashes or after `./stop.sh`.
Turn it off: `launchctl bootout gui/$(id -u)/local.tradingdesk.autostart` and delete the plist.

## Strategy lab (2026-11-19)

Lab page on the desk. One paper profile per strategy so they can be compared over the same weeks; review date 2026-12-19.

| Profile | Runs as | Candles | Backtest 2021-22 / 2023-24 (profit factor) |
|---|---|---|---|
| 20-day momentum (baseline) | `LabTSMomentum` | 4h | 1.34 / 1.57, unseen 2025-26 1.44 |
| Swing bot (same rule under the desk Rules) | the main bot | 4h | as above; not like-for-like |
| Daily momentum | `LabDailyMomentum` | 1d | 3.45 / 1.65, unseen 2025-26 1.63 |
| Relative strength, long 2 / short 2 | `LabRelStrength` | 1d | 1.22 / 1.35, 2025-26 not run |
| Momentum with a funding crowding filter | `LabFundingTrend` | 4h | 1.28 / 1.61 (no better than plain momentum) |
| Scalp: zone touch plus delta flip | the desk's scalp bot | 15m | failed (docs/order-flow-test.md) |
| Random entries (control) | `LabCoinFlip` | 4h | 0.79 to 1.16 over five random sequences |

- `./lab.sh start | stop | status`; `./start.sh` and `./stop.sh` include it, so the login auto-start covers the lab.
- Each lab bot is its own freqtrade paper instance: `user_data/config.json` plus `lab/cfg/<id>.json` (own 1,000 USDT wallet, own database in `lab/db/`, 4 trades at most, API off, `dry_run` on). Strategies: `user_data/strategies/lab.py`.
- Lab bots ignore the desk Rules: risk is fixed at 1% a trade, the daily limits do not apply, and they are not in the desk's account total. The swing bot still follows the Rules.
- Cards (idea, entry, stop, exit, backtest, decision rule): `lab/profiles.json`. The page reads the databases only (`ui/lab.py`, `/api/lab`).
- Results are in R: profit divided by what the trade risked at its first stop. Open-trade profit on the page leaves out fees and funding.
- `lab/uptime.json` logs stretches when the desk was not running (signals are missed while the Mac sleeps); `lab/history.jsonl` gets one line per profile per UTC day.
- Backtest lines: `lab/backtests.tsv`. 2025-26 was not used for the two new strategies.
- These strategies take few trades: about 15 a month on 4h and 6 on daily candles. A month shows whether paper matches the backtest; it cannot show whether a strategy makes money. Each card names the trade count at which it is judged.

## Flush fade (2026-11-19)

Fading large one-way candles on heavy one-sided volume (a proxy for liquidation flushes), 5m and 15m, 8 variants, plan frozen first: all eight lose after fees in both halves of 2022-07 to 2025-06 (-0.19 to -0.30 R per event; about zero before fees). Not added to the lab. Details: docs/flush-fade-test.md. With the lab's two new strategies this makes about 28 variants tried on this data, not counting the 1,152 zone-and-delta variants.

## Lab health check (2026-11-19)

`.venv/bin/python lab_health.py` prints whether every lab bot and the desk are running, trades opened and closed in the last 24 hours, missed time and error lines, and appends the report to `lab/health.log` (exit code 1 when something needs attention). It reads only. A scheduled Claude task ("Trading lab: daily health check", daily at about 09:05) runs it and runs `./start.sh` when something is down.

## Intraday, 1 hour to 3 days (2026-11-19)

Plan and results: `docs/intraday-test-2.md`; strategies in `user_data/strategies/intraday2.py`. Seven variants on 15m and 1h candles (pullback and breakout entries inside the 20-day trend, 1h relative strength, Bitcoin leads) plus an hour-of-day screen. None passed: profit factors 0.86 to 1.08 after fees in 2021-22 and 2023-24, while random entries inside the same trend reached 1.21. Nothing was added to the lab.

## Open interest (2026-11-19)

Plan and results: `docs/open-interest-test.md`. Data: `backtest/oi_data.py` downloads Binance's 5-minute open interest into `user_data/data/oi/` (137 MB); event study in `backtest/oi_bt.py`; strategies in `user_data/strategies/oi_flush.py` and `oi_trend.py` (backtest only). Fading a sharp 4-hour move that came with falling open interest beat the same move without it by about 1% per event over 72 hours, on the long side only, but as a strategy with a stop it did not beat random entries (profit factor 0.92 / 1.03). Nothing was added to the lab.

## Long/short account ratio (2026-11-19)

Plan and results: `docs/long-short-ratio-test.md`; event study `backtest/ls_bt.py`; research strategies `user_data/strategies/ls_crowd.py`. Trading against the all-accounts ratio (long in its lowest 5% of 90 days, short in its highest 5%, closed after 24 hours) gave profit factor 1.43 / 1.32 in training and 1.15 on the held-back period, where random entries gave 0.79 to 0.90. It runs as the lab profile `crowdfade` (`LabCrowdFade`), which keeps its own ratio history in `lab/ratio/`. The 72-hour version and the top-trader ratio failed.
