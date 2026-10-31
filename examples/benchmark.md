# Matched comparison with backtesting.py

`compare_bt.py` verifies equivalent accounting before `benchmark.py` compares
speed. These replace the earlier scripts that used different position sizes,
fill timing, and SMA calculations and only printed approximate results.

## Run the comparison

From the repository root, with your Python environment activated:

```bash
python -m pip install ".[report]" "backtesting==0.6.6"
repo_dir="$PWD"
cd /tmp
python "$repo_dir/examples/compare_bt.py" --commission 7
python "$repo_dir/examples/benchmark.py" \
  --sizes 2000 10000 50000 --commissions 0 7 --repeats 5 \
  --output matched-comparison.json
python -m unittest discover -s "$repo_dir/tests" -p test_reference_comparison.py
```

The FX extension uses maturin's default release build. Reinstall after source
changes and run outside the checkout to avoid loading an old local extension.
The reference dependency is optional; the comparison tests skip when it is not
installed. The JSON report records library versions, CPU, OS, Python, Rust,
source HEAD, every timing sample, medians, and matching trade counts/equity.

## Shared strategy and execution rules

Both libraries use identical OHLCV values and UTC timestamps from the bundled
EURUSD data. Larger datasets tile that sample and regenerate a monotonic hourly
index. They are scaling fixtures, not additional independent historical data.

- Compute the same trailing SMA 10/50 decisions once with pandas before either
  library runs. The precomputation uses past and current closes only. The warmup
  is flat. Both callbacks read this shared array without recalculating indicators.
- Start with `$10,000` and use exactly `1,000` units, equivalent to `0.01` FX lots
  with contract size `100,000`. Keep at most one long position. No shorting,
  pyramiding, stops, or targets are used.
- Enter when the fast SMA is above the slow SMA while flat. Exit when it is
  below while long. Decisions at bar `i` fill at bar `i+1`'s open.
- Queue a final close from the penultimate bar so both libraries finish flat at
  the final bar's open. Do not open a new position from the final two decisions.
- Use USD quote/account currency with conversion rate `1`. Test commission `0`
  and `$7` per lot per side. The reference commission callable charges
  `abs(units) * 7 / 100000` per fill, matching the FX commission model.
- Set spread to zero. The libraries define nonzero spread differently, so this
  comparison does not claim equivalence for nonzero spread.

backtesting.py uses its ordinary queued market orders with
`trade_on_close=False`, `hedging=False`, and no automatic finalization. A
standalone opposite order flattens the long. Backtestingfx's normal `buy()` and
`close_all()` fill immediately at the current close, so the comparison strategy
explicitly applies the previous decision through `Broker.buy()` or
`Broker.close_all()` at the current open. This is comparison strategy code,
not a new next-open engine option. Neither library's broker implementation is
patched. Both execute via their normal public `Backtest.run()` entry point.
See the [reference execution options](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html).

The reference enforces margin while FX currently does not. Fixed exposure on
these datasets remains affordable, and the equivalence checks fail if that
difference affects executions. This does not establish margin-model equivalence.

## What must agree

Before timing, and again after every measured pair, assertions compare trade
count, direction, size, entry/exit timestamps, entry/exit prices, per-trade net
PnL, the entire bar-by-bar equity curve, final equity, return, win rate for
nonempty trades, and maximum drawdown. Monetary/price comparisons use absolute
tolerance `1e-8`, not rounded output. Unit sizes use `1e-10` tolerance.

FX prepends an initial-cash observation, so its first extra observation is
removed when aligning curves. Reference drawdown is negative while FX reports
its positive magnitude. No prices, timestamps, or curve observations are shifted
to make the assertions pass.

A seven-bar fixture has distinct opens and closes and two hand-calculated trades.
It checks next-open fills, final-open liquidation, commission, and every equity
observation. With commission `7`, trades enter at `1.2` and `1.5`, exit at `1.3`
and `1.6`, and each nets `$99.86`, ending at `$10,199.72`. A separate test
deliberately corrupts an entry price and proves the checker rejects it despite
the unchanged trade count.

This establishes equivalent accounting for this strategy and these settings.
It does not establish equivalence for arbitrary strategies, SL/TP policies,
leverage, nonzero spread, financing, or all reported statistics. For example,
the libraries use different Sharpe/annualization definitions.

## Timing boundaries

Load/normalize the input and compute the shared SMA decisions once outside
both library timers. Record this common preparation separately in JSON.
For each sample, construct a fresh `Backtest` and strategy and call `run()`.
Alternate library order between repetitions. There is one untimed warmup and
five measured pairs per dataset and commission setting. Report medians.

- **Setup:** from before the `Backtest` constructor through the end of strategy
  `init()`. This includes each library's validation and data conversion, and FX's
  DataFrame-to-Bar and initialization-history conversion.
- **Run:** from the end of `init()` until `run()` returns, including simulation,
  finalization, and that library's normal statistics calculation.
- **Total:** the entire constructor plus `run()` call. This is the primary number
  for deciding which library completes this workload faster.

The timing marker executes once during initialization. Assertions and printing
are outside the timing. The libraries compute different numbers of statistics;
the run-phase ratio is not a pure engine-loop speedup. Neither plotting nor
optimization is measured. Use [benchmark_callbacks.py](benchmark_callbacks.md)
to isolate callback overhead inside backtestingfx.

## Local results

Measured on an Intel Core i5-8350U, Linux x86_64, CPython 3.14.4, Rust 1.98.1,
PyO3 0.28.3, pandas 3.1.0, NumPy 2.5.4, and backtesting.py 0.6.6. The installed
FX release build includes the issue #48 callback optimization and lightweight
position properties. All equivalence checks passed for every measured pair.

| Bars | Commission per lot/side | Matching trades | Final equity |
| ---: | ---: | ---: | ---: |
| 2,000 | 0 | 34 | 9975.09 |
| 2,000 | 7 | 34 | 9970.33 |
| 10,000 | 0 | 174 | 9862.73 |
| 10,000 | 7 | 174 | 9838.37 |
| 50,000 | 0 | 874 | 9300.93 |
| 50,000 | 7 | 874 | 9178.57 |

Times below are milliseconds. Setup, run, and total are independently calculated
medians, so rounded setup plus run may not exactly equal the total.

| Bars | Commission | backtesting.py setup | Run | Total | backtestingfx setup | Run | Total |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2,000 | 0 | 1.1 | 20.0 | 21.1 | 79.3 | 1.4 | 80.7 |
| 2,000 | 7 | 1.2 | 20.7 | 21.9 | 75.5 | 1.4 | 76.9 |
| 10,000 | 0 | 1.3 | 56.3 | 57.6 | 393.1 | 7.2 | 400.3 |
| 10,000 | 7 | 1.2 | 54.9 | 56.0 | 383.7 | 7.5 | 391.9 |
| 50,000 | 0 | 1.5 | 240.0 | 241.5 | 1970.0 | 42.3 | 2011.6 |
| 50,000 | 7 | 1.3 | 237.6 | 239.1 | 1925.1 | 40.5 | 1967.8 |

For 50,000 bars with commission, backtestingfx's run phase was about 5.9 times
faster, but its total was about 8.2 times slower. Setup, especially row-by-row
DataFrame ingestion, dominated. Optimizing that conversion is tracked in #39.
These results do not support a claim that backtestingfx is faster end to end
for this workload. Timing varies with versions, hardware, and system load.
