# Market-data validation

Python `Backtest`, `Bar`, and `Engine` inputs and the native CSV loader check
market data before simulation. The same native rules cover list-based engines
and bulk column ingestion. Valid input values are copied into owned native bars;
the event loop and optimization workers do not repeat these checks.

## Accepted data

`Backtest` requires a pandas DataFrame. OHLC columns are required; volume is
optional and defaults to zero. OHLCV and timestamp column names are
case-insensitive. Duplicate names after normalization are rejected, including
duplicates among extra columns.

Each bar must satisfy:

| Field | Requirement |
| --- | --- |
| `open`, `high`, `low`, `close` | Real, finite, and greater than zero |
| OHLC range | `low <= open <= high` and `low <= close <= high` |
| `volume` | Real, finite, and nonnegative |
| Timestamp sequence | Strictly increasing after conversion to int64 Unix seconds |

Flat candles are valid. Zero volume is valid. Numeric strings and nullable
numeric columns without missing values remain supported. Complex prices,
missing values, NaN, infinity, negative volume, and inconsistent ranges raise
`ValueError`. Unsupported top-level input types raise `TypeError`.

Errors identify the zero-based bar position and failing field, for example:

```text
bar 1: close must be positive and finite
bar 1: OHLC must satisfy low <= open/close <= high
bar 1: timestamps must be strictly increasing in whole seconds; duplicate or out-of-order timestamp
```

An empty DataFrame with the required OHLC columns is valid even without a
timestamp column. Empty engines return no trades, unchanged cash, and a
one-element equity curve containing initial cash. Plotting an empty run still
raises an error because there are no bars to display.

## Timestamp sources and ordering

A DatetimeIndex supplies the timestamps when present. Otherwise provide a
timestamp column. Existing Timestamp values in an object index keep their
precedence over that column. Missing timestamps, NaT, invalid datetime values,
and values that cannot convert to int64 seconds produce actionable errors.

This change preserves the existing timestamp conversion. Numeric timestamp
columns still use pandas' implicit units, and fractional timestamp rounding
remains unchanged. Explicit numeric units and a common timezone policy across
loaders remain tracked in [#17](https://github.com/KhizarImran/backtestingfx/issues/17).

Ordering is checked on the converted engine timestamps. Two distinct
subsecond observations that collapse to the same second are therefore rejected.
The engine does not sort, deduplicate, aggregate, or repair data silently.
Prepare the intended chronological series before starting a backtest; choose
an explicit aggregation or duplicate-resolution policy suitable for your data.

These rules apply with or without swaps. Enabled swaps additionally require
timestamps within the supported rollover calendar range. See
[SWAPS.md](SWAPS.md) for the financing calendar and ordering relative to stops.

## Python execution boundaries

Validation completes before strategy construction/initialization in
`Backtest.run()` and before `signal_fn` in `Backtest.optimize()`. Each call reads
the current input again, so edits between runs are checked. Signal functions
must treat their DataFrame argument as read-only.

Direct Python `Bar(...)` construction checks one candle's prices, range, and
volume. Direct `Engine([...], ...)` construction also checks timestamp order.
The private column factory uses the same rules while building native bars,
without temporary Python Bar objects. Invalid inputs do not invoke strategy
callbacks or return partially constructed engines.

## Native Rust and CSV input

The native CSV loader now returns `Result<Vec<Bar>, DataError>` instead of
panicking. CSV columns are resolved by case-insensitive name; timestamp need
not be the first column, and volume is optional. Timestamps retain the existing
RFC 3339 parser. Header-only input with the required columns returns an empty
series. Missing/duplicate columns, bad fields, invalid records, I/O failures,
and timestamp-order problems return typed errors.

Existing native callers must handle the result:

```rust
use _backtestingfx::data::load_csv;

let bars = load_csv("data/EURUSD_1H.csv")?;
```

Native code constructing bars itself can use `Bar::try_new`,
`Engine::try_new`, or `data::validate_bars`. These APIs return `DataError` for
invalid market data. Native broker configuration remains caller-owned.
Existing infallible Rust `Bar::new` and `Engine::new` remain available for
trusted internal data. Callers using them, or editing public `Engine.data`,
are responsible for maintaining these invariants before execution.

## Migration and verification

Previously accepted malformed candles and duplicated/reversed timestamps now
raise errors. Subsecond data must not collide at the engine's whole-second
resolution. Missing data now raises a descriptive `ValueError` rather than an
incidental KeyError or scalar conversion exception. The earlier swap-only
allowance for repeated Python engine timestamps no longer applies.

Regression tests first failed against main `acffca8`, including invalid prices
and duplicate timestamps that did not raise. The checked implementation passes
72 installed-package Python tests, including the optional reference fixtures,
and 48 Rust tests. Clippy with warnings denied and the native CSV example pass.
Valid-input tests retain case variants, optional volume, timezone indexes,
numeric timestamp promotion, unaligned/strided/read-only buffers, independent
runs, report snapshots, and financing reconciliation.

The existing matched benchmark was run in release mode before and after, with
one warmup and five samples at each size. Every pair verifies trade sizes,
fills, timestamps, net PnL, final cash, and the full equity curve against
backtesting.py 0.6.6. Swaps are disabled for that matched fixture.

| Bars | Ingestion before | Ingestion after | Total before | Total after |
| ---: | ---: | ---: | ---: | ---: |
| 50,000 | 1.428 ms | 2.186 ms | 41.755 ms | 41.312 ms |
| 100,000 | 2.305 ms | 3.973 ms | 83.843 ms | 80.980 ms |

Validation adds measurable ingestion work; differences in simulation timings
are noise, not a claimed speedup. The recent ingestion improvement remains
intact. Whole-process peak RSS was 130.20/130.79 MiB before/after at 50k bars
and 149.16/150.82 MiB at 100k bars.

Environment: Intel Core i5-8350U, Linux x86_64, CPython 3.14.4, Rust 1.98.1,
PyO3 0.28.3, pandas 3.1.1, NumPy 2.5.4, and the fixture and cost settings from
[the ingestion benchmark](examples/benchmark_ingestion.md). Reproduce with
the installed release package outside the checkout. From the repository root
with a virtual environment activated:

```bash
python -m pip install ".[report]" "backtesting==0.6.6"
repo_dir="$PWD"
cd /tmp
python "$repo_dir/examples/benchmark_ingestion.py" \
  --sizes 50000 100000 --repeats 5 --label validation --output validation.json
```
