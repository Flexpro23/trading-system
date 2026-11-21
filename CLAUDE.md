# Project rules

## Backtest output

- Save backtest results as compact summaries (text, CSV, JSON or parquet with only the needed columns) in `backtest/reports/`, and commit them.
- Do not keep full `.pkl` dumps unless the user asks. `backtest/out/` is for intermediates while a test runs; `backtest/run.sh` deletes them after the report (`KEEP_RAW=1` keeps them).
- A re-run overwrites the earlier run. Do not keep copies in an `old/` folder.
- Record each test in `backtest/RESULTS.md`: what it tested, key numbers, conclusion, and the command, parameters, data range and commit needed to regenerate it.
