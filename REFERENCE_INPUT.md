# Reference DataFrame input

Use `Backtest(..., data_mode="reference")` to prepare pandas input with the
index policy of backtesting.py 0.6.6. The default `data_mode="fx"` follows the
[strict FX input rules](MARKET_DATA.md).

```python
import pandas as pd
from backtestingfx import Backtest, Strategy

class Observe(Strategy):
	def init(self):
		print(self.data.index)       # Full prepared index
		print(self.data.df.Signal)   # Aligned custom column

	def next(self):
		print(self.data.index[-1])   # Current source label
		print(self.data[-1].close)

data = pd.DataFrame({
	"Open": [1.1, 1.2, 1.3], "High": [1.1, 1.2, 1.3],
	"Low": [1.1, 1.2, 1.3], "Close": [1.1, 1.2, 1.3],
	"Signal": [0, 1, 0],
})
bt = Backtest(data, Observe, data_mode="reference")
stats = bt.run()
print(stats.equity)              # Series with the prepared RangeIndex
```

This option covers input and index identity. Strategies use the FX order API,
lot sizing, and costs. To opt in to the reference market timeline, also pass
`trade_on_close` or `finalize_trades`. The full facade is tracked in
[#61](https://github.com/KhizarImran/backtestingfx/issues/61).
[Queued market execution](README.md#queued-market-execution)

## Input policy

| Input | Reference mode |
| --- | --- |
| Columns | Exact `Open`, `High`, `Low`, `Close`; custom columns retained |
| Missing `Volume` | Added as NaN without changing the caller's frame |
| Empty frame, missing OHLC column, null OHLC | `ValueError` before callbacks |
| Other numeric OHLC and volume values | No strict FX positivity, finiteness, or candle-range checks |
| Unsorted index | Warn and sort the entire frame, retaining column alignment |
| Duplicate labels | Retained as separate rows |
| RangeIndex | Period labels retained, with a non-datetime warning |
| DatetimeIndex | Precision, timezone, name, duplicate labels, and NaT retained |
| Other numeric index | Reference heuristic converts to datetimes if more than 80% of labels exceed the Unix timestamp for 1975 |
| Other non-datetime index | Period labels retained, with a warning |
| `timestamp` column | Custom data; never overrides the index |

The numeric-index heuristic uses pandas' implicit nanosecond interpretation.
It does not infer seconds. `timestamp_unit` configures only FX mode. Prefer an
explicit DatetimeIndex when your numeric labels represent actual times.

The baseline is [backtesting.py 0.6.6 at commit
9c2b8e1](https://github.com/kernc/backtesting.py/blob/9c2b8e1c99f3a860883222ba8d6d5b5cac3a932d/backtesting/backtesting.py).
Its generic-index code uses pandas APIs removed in pandas 3. This implementation
uses supported APIs for the same heuristic, so those inputs work on pandas 3.
Permissive input acceptance does not guarantee a valid order at every price.
Broker validation still applies when a strategy trades.

## Strategy and result labels

During `init()`, `self.data` exposes the full prepared history. During
`next()`, it exposes the prefix through the current row. `self.index` is the
zero-based row position.

- `self.data.Close` and `self.data["Close"]` return the same NumPy view for
  the current callback. The five names are case-sensitive and use the input
  column dtypes. Numeric indexes and slices on `self.data` still return bars.
- `float(self.data.Close)` and `bool(self.data.Close)` use the last visible
  close. `self.data.pip` uses the median number of decimal places in the full
  close column, following backtesting.py 0.6.6.
- `self.data.Close.s` gives a named pandas Series. As in the reference,
  converting a sliced array to pandas uses the first index labels for its
  length. Slice `.s` with `.iloc` to keep the labels from a specific window.
- `self.data.index` and `self.data.df` retain source labels, and custom
  columns stay aligned in `.df`. Treat the views as read-only.

Indicator registration and named custom-column arrays are tracked in
[#20](https://github.com/KhizarImran/backtestingfx/issues/20) and
[#22](https://github.com/KhizarImran/backtestingfx/issues/22).

Each run owns its frame and index snapshot. Strategy bar `timestamp` values and
position `entry_time` values use source labels. Results retain native numeric
statistics and expose these indexed views:

- `stats.index` is the prepared index.
- `stats.equity` is a pandas Series with one observation per source row.
- `stats.trades` have `entry_bar`, `exit_bar`, `entry_time`, and `exit_time`.
- `stats.order_rejections` and `stats.liquidation` have `bar` and `time`.

The existing `timestamp`, `entry_timestamp`, and `exit_timestamp` attributes also
use source labels in this mode. Row positions disambiguate repeated labels.
`equity_curve` retains the existing list with initial cash prepended. The full
pandas results schema remains tracked in
[#32](https://github.com/KhizarImran/backtestingfx/issues/32).

Optimization signals receive the prepared frame in the same row order as the
engine. Each optimization result retains that index. Signal functions must
treat their frame argument as read-only.

## Reports and financing

Reports plot one candle per row position, including repeated timestamps, and
show exact source labels in ticks, hover text, and ledgers. Datetime labels keep
their nanoseconds and timezone offset. Period durations use bar counts. Reports
use the successful run's snapshot rather than re-reading the caller's index.

Reference mode rejects `SwapConfig`. Native execution uses dense row positions
internally, which cannot represent financing rollover times. Use FX mode with
an explicit chronological time source for [overnight financing](SWAPS.md).
Direct Python `Bar`, list-based `Engine`, and native CSV input keep their strict
FX validation.

## Verification

Install `.[report,test]` to include the pinned reference and reporting packages.
CI installs both extras and runs `tests/test_reference_input.py` without skipping
the reference dependency. Fixtures cover periods, duplicate labels, subsecond
and DST timestamps, validation, sorting, features, trades, equity, optimization,
margin events, and report snapshots. `tests/test_execution_timing.py` compares
queued fills, callback timing, finalization, and equity with the pinned reference.

The initial 12 input tests failed on the previous main because `data_mode` was
unsupported. After implementation, all 15 input tests, 107 installed-package
Python tests, and 56 Rust tests pass locally. Clippy passes with warnings denied.

The existing release benchmark verifies matched trades and full equity against
backtesting.py. Five samples after one warmup retain the existing FX runtime:

| Bars | FX total before | FX total after | Equivalence |
| ---: | ---: | ---: | --- |
| 50,000 | 44.436 ms | 44.745 ms | Passed |
| 100,000 | 92.750 ms | 85.480 ms | Passed |

These timings check for regression; they do not establish a speedup. Both runs
used Linux x86_64, an Intel Core i5-8350U, CPython 3.14.4, Rust 1.98.1,
pandas 3.1.1, NumPy 2.5.4, and backtesting.py 0.6.6. The baseline was main
`e29dcff`. Run this from outside the checkout after installing a release build:

```bash
python /path/to/backtestingfx/examples/benchmark_ingestion.py \
	--sizes 50000 100000 --repeats 5 --label reference-input --output input.json
```
