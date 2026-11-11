# Ingestion and total backtest performance

Issue #53 removes the setup bottleneck measured in the
[original matched comparison](benchmark.md). On this machine, the same backtest
now completes about 48–50 times faster than the previous implementation and
5.5–5.7 times faster than backtesting.py. These are total constructor-and-run
times for the specific strategy and data below, including setup and statistics.

## Matched results and timings

All times are seconds, measured on November 11, 2026. Each cell is the median
of five samples after an untimed warmup. Before is main commit `9fe8523`;
after is the issue #53 implementation. Each size runs in a fresh child process.

| Bars | FX setup before | FX setup after | FX total before | FX total after | Total speedup |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 50,000 | 1.856924 | 0.003946 | 1.894287 | 0.039755 | 47.6x |
| 100,000 | 3.941711 | 0.007876 | 4.021479 | 0.080230 | 50.1x |

The reference was measured again alongside the final build, alternating library
order. Every measured pair passes the trade and full-equity comparison.

| Bars | Trades | Final equity | backtesting.py total | FX total | FX speed ratio |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 50,000 | 874 | 9,178.57 | 0.226282 | 0.039755 | 5.69x |
| 100,000 | 1,749 | 8,353.82 | 0.438763 | 0.080230 | 5.47x |

The strategy, sizing, and fill rules are identical to the original comparison:
precomputed SMA 10/50 decisions, 1,000 units, long only, next-open market fills,
final-open liquidation, zero spread, and $7 commission per lot per side. Both
libraries start with $10,000 and use USD quote/account currency. The bundled
EURUSD sample is tiled with a fresh UTC hourly index. It is a scaling fixture,
not additional historical data. Shared CSV loading and indicator preparation
happen before timing. Results do not establish equivalence or a speed advantage
for every strategy or execution policy.

## What the profile showed

A separate no-op workload isolates the setup components. Ingestion includes
column resolution, timestamp and numeric conversion, and construction of an
owned native engine. History includes Python Bar allocation and the call into
an otherwise empty initialization method.

| Component | 50k before | 50k after | 100k before | 100k after |
| --- | ---: | ---: | ---: | ---: |
| DataFrame report snapshot | 0.000292 | 0.000334 | 0.000465 | 0.000539 |
| Ingestion and engine construction | 1.833340 | 0.001311 | 3.749694 | 0.002415 |
| Initialization history | 0.002885 | 0.002022 | 0.006561 | 0.004070 |
| No-op callbacks and statistics | 0.007628 | 0.007300 | 0.016528 | 0.015221 |

Medians are calculated independently, so component medians need not sum to a
reported total. The setup probe uses no-op direct Engine callbacks; the matched
SMA timings above include the normal Python Strategy adapter.

An independent cProfile of the original 50k run recorded 9.60 million calls.
`_to_bars` accounted for 5.482 seconds of the 5.594-second profiled run.
`iterrows` and per-row Series construction accounted for most of that work,
followed by Series scalar lookups. The final profile recorded 0.303 million
calls and 0.106 seconds total, with `_engine` at about 0.003 seconds. Python
strategy callbacks now dominate. Profiling adds overhead; these profile times
are excluded from the benchmark medians and peak-memory samples.

## Design and alternatives

Two independent design checks compared cheaper Python row iteration with bulk
native ingestion. Bulk ingestion won on total ingestion cost and temporary
object allocation. The skill's preferred model families were unavailable, so
both checks used the available model and there was no cross-family judge.

`Backtest._bar_columns(df)` owns pandas column resolution and conversion.
For numeric OHLCV with a DatetimeIndex, it prepares contiguous, aligned,
native-endian arrays. `Engine._from_columns(columns, config...)` reads typed
PyO3 buffers into its own `Vec<Bar>`. It checks shape, equal lengths, layout,
byte order, and configuration before assembling bars. It retains no borrowed
array storage and keeps the GIL while copying. There is no new dependency,
NumPy C ABI requirement, or unsafe Rust code.

The row design contributed the compatibility fallback: other dtypes and
timestamp columns use the same common row dtype as the original converter,
without allocating a pandas Series or temporary Python Bar for each row.
This preserves numeric timestamp promotion, scalar `float()` behavior, and
which invalid row fails first. Whole-second DatetimeIndex values use vector
conversion; fractional and very distant timestamps retain pandas' scalar
rounding and overflow behavior. String timestamp columns and object dtypes
therefore have a lower speed ceiling than the numeric/DatetimeIndex fixture.

The public `Backtest`, `Strategy`, and list-based `Engine` constructor stay the
same. A run still copies the DataFrame and reads its current values. There is no
converted-data cache to invalidate. Initialization still receives a fresh list
of fresh Bars, and callbacks still receive distinct Bars. History construction
uses an iterator instead of cloning an intermediate native Vec. Retained Bars,
history restrictions, reports, and later runs keep their existing behavior.

The standalone candidate benchmark measures conversion plus native construction,
with one warmup and five measured samples. It validates all fields outside the
timer.

| Bars | Python zip | Python itertuples | Bulk ingestion |
| ---: | ---: | ---: | ---: |
| 50,000 | 0.104472 | 0.093089 | 0.001632 |
| 100,000 | 0.200611 | 0.200404 | 0.002933 |

Keeping Python Bar creation in the ingestion path was rejected. Borrowed engine
storage, cached conversions, and a lazy history proxy were also rejected because
they complicate input mutation or change strategy-visible object semantics.

## Memory and compatibility checks

Whole-process peak RSS was 127.45 to 126.41 MiB at 50k bars, and 145.41 to
143.63 MiB at 100k bars. These small differences do not establish a general
memory improvement. Each process includes pandas, the reference library,
DataFrames, repeated runs, and correctness checks. Contiguity, alignment, or
dtype conversion can allocate temporary arrays. The engine owns a native copy;
Python history remains an additional allocation during ordinary strategy runs.

Regression tests compare against the old converter, including all OHLCV fields,
case variants, missing volume, strided and byte-swapped arrays, unaligned and
read-only columns, nullable/string values, timezone and datetime resolutions,
pre-epoch fractions, numeric timestamp promotion, errors, and empty inputs.
They also cover repeated input changes, report snapshots, native ownership,
history identity, and malformed private buffers. The byte-order regression
failed before the explicit guard: PyO3 0.28.3 accepted foreign-endian buffers
and interpreted their bytes as native values. The guarded build passes.

The installed release package passes 51 Python tests, including the optional
reference fixtures; 35 Rust tests and Clippy with warnings denied also pass.
Broader OHLC validity and timestamp-policy changes remain in #15 and #17.

## Reproduce the measurements

The machine used an Intel Core i5-8350U, Linux 7.0.2 x86_64, CPython 3.14.4,
Rust 1.98.1, PyO3 0.28.3, pandas 3.1.0, NumPy 2.5.4, and backtesting.py 0.6.6.
Both FX packages used maturin's release build. Raw timing samples and metadata
are in [benchmark_ingestion_results.json](benchmark_ingestion_results.json).
The original metadata could not locate rustc on PATH; its version is recorded
here from the toolchain used to build the packages.

With a virtual environment activated, run these from the repository root:

```bash
python -m pip install ".[report]" "backtesting==0.6.6"
repo_dir="$PWD"
cd /tmp
python "$repo_dir/examples/benchmark_ingestion.py" \
  --sizes 50000 100000 --repeats 5 --label after \
  --output ingestion-after.json --profile ingestion-after.prof
python "$repo_dir/examples/benchmark_ingestion.py" --candidates \
  --sizes 50000 100000 --repeats 5 --output ingestion-candidates.json
python -c 'import pstats; pstats.Stats("ingestion-after.prof").strip_dirs().sort_stats("cumulative").print_stats(20)'
python -m unittest discover -s "$repo_dir/tests"
```

To reproduce before, install `9fe8523` into a separate environment and run this
same benchmark script against that installed package with `--label before`.
Run outside either checkout to avoid loading a stale local extension. The
`--candidates` flag requires the new bulk factory and is for the after build.
Keep the same interpreter, dependencies, machine, and release profile when
comparing results.
