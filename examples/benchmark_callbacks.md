# Python callback benchmark

This benchmark measures the Rust-to-Python callback path separately from data
conversion. It also compares indicator work, trading, and position inspection.
It uses the real Python `_Adapter` and the normal Rust broker, equity, statistics,
and final-liquidation code. It does not compare different backtesting libraries.

## Run the benchmark

From the repository root, with your Python environment activated:

```bash
python -m pip install .
repo_dir="$PWD"
cd /tmp
python "$repo_dir/examples/benchmark_callbacks.py" \
  --sizes 1000 10000 100000 --repeats 7 --conversion-repeats 3 \
  --label after --output callback-after.json
```

The normal pip build uses maturin's release profile. Reinstall after Rust or
Python source changes. Run outside the checkout to use the installed extension.
Keep correctness assertions enabled; do not run with `python -O`.
Use `--cases native-noop direct-noop adapter-noop` for a narrower comparison.

Each dataset runs in a fresh subprocess. JSON output records all simulation
samples, medians for every phase, peak RSS, CPU, OS, Python, Rust, dependency
versions, the Cargo.lock PyO3 version, and the source checkout's HEAD. The HEAD
identifies the source checkout, not the installed wheel. Use `--label` to identify
working-tree builds, and make sure the installed wheel matches your source.
Peak RSS is unavailable on platforms without Python's `resource` module.

## Timing boundaries

All paths use the same prepared midpoint bars and cost configuration. Synthetic
prices are flat at `1.1`, with a `0.0001` spread, commission `7` per lot per side,
contract size `100000`, and quote-to-account conversion `1.27`.

1. **Conversion:** time `Backtest._to_bars()` separately. DataFrame construction
   is outside this measurement. Use the last conversion's bars for every case.
2. **Engine setup:** construct a fresh `Engine` from those bars for every sample.
   Record this separately from the run. Construct a fresh strategy and adapter
   outside the run timer too.
3. **Initialization:** start the run timer before `Engine.run()`. A single timestamp
   at the end of the real adapter's `init()` marks the boundary. This includes
   Rust-to-Python conversion of the initialization history and any indicator
   precomputation. Broker creation follows this boundary and is part of the run.
4. **Simulation:** measure from that timestamp until `Engine.run()` returns.
   Include callbacks, SL/TP scans, equity, final liquidation, statistics, and
   conversion of results back to Python. No timing calls are added per bar.

The native no-op has no Python initialization. Its measured run includes its
empty native `init()` and the normal engine setup inside `Engine::run()`, plus
the same broker, equity, finalization, and statistics paths. The private
`Engine._run_native_noop()` method is a benchmark hook, not a supported strategy API.

There is one untimed warmup followed by seven measured runs per case. Reported
times are medians. No engines, strategy instances, or brokers carry state between
samples. Correctness assertions run after timing. They check trade count, fills,
net PnL, final cash, equity length and endpoints, no-op equity, final indicator
values, and position inspection. Trading uses fixed `0.001` lots.

Peak RSS is the whole dataset subprocess's high-water mark after all cases,
including imports, conversion, bars, equity, trades, and correctness checks. It is not per-case
allocation or the memory cost of a single callback. Native memory is included.

## Local results

Measured on an Intel Core i5-8350U, Linux x86_64, CPython 3.14.4, Rust 1.98.1,
PyO3 0.28.3, pandas 3.1.0, and NumPy 2.5.4, with default Cargo release settings.
Both versions used the same dependency versions, benchmark, data, and costs.
The baseline used dispatch from `d9687e7` with the native no-op benchmark hook
added. The compatible version reuses the Python method-name string and adds
position count/presence properties. It keeps dynamic callback lookup and fresh
callback Bar objects.

| Bars | Adapter no-op before (ns/bar) | After (ns/bar) | Run-time reduction |
| ---: | ---: | ---: | ---: |
| 1,000 | 290.7 | 201.4 | 30.7% |
| 10,000 | 294.8 | 204.4 | 30.7% |
| 100,000 | 311.1 | 229.7 | 26.1% |

At 100,000 bars, the compatible build measured:

| Workload | Initialization (ms) | Simulation (ms) | Simulation (ns/bar) |
| --- | ---: | ---: | ---: |
| Native no-op | 0.00 | 1.22 | 12.2 |
| Direct Python no-op | 6.63 | 15.77 | 157.7 |
| Python adapter no-op | 5.23 | 22.97 | 229.7 |
| Rolling 20-bar mean in `next()` | 4.76 | 330.45 | 3304.5 |
| Precompute mean in `init()` | 15.80 | 36.60 | 366.0 |
| Open and close one trade per bar | 4.98 | 83.81 | 838.1 |
| Hold 32 positions without inspection | 3.10 | 38.71 | 387.1 |
| Read `len(self.positions)` with 32 positions | 3.00 | 230.73 | 2307.3 |
| Read `self.position_count` with 32 positions | 3.01 | 52.87 | 528.7 |
| Read `self.has_positions` with 32 positions | 3.09 | 54.81 | 548.1 |

The direct-to-adapter difference is about 72 ns/bar here. It includes adapter
state updates and the extra `Strategy.next()` dispatch. List inspection is much
more expensive with 32 positions because it copies the position vector and
creates Python position objects. The presence workload takes about 4.2 times
less simulation time than list inspection on this fixture. The read workloads
share the same subclass dispatch; the hold-only case omits that extra method call.

At this size, constructing the engine for the adapter no-op took about 4.92 ms,
and DataFrame-to-Bar conversion took 3.93 seconds. The 26.1% reduction applies
to the measured callback run, not to a complete `Backtest.run()`. Conversion
still dominates that workload and is tracked separately in #39.

| Bars | Peak RSS before (MiB) | After (MiB) |
| ---: | ---: | ---: |
| 1,000 | 78.53 | 78.37 |
| 10,000 | 83.50 | 84.19 |
| 100,000 | 135.88 | 135.82 |

These whole-process high-water marks do not establish a memory improvement.
The after run includes the new count/presence cases too. Hardware, allocator
state, Python version, and system load affect timings. Differences between rows
estimate workload costs; they are not additive profiler measurements.

## Dispatch and allocation experiments

Exploratory runs used 100,000 bars, nine repetitions, and one conversion sample,
with only `native-noop`, `direct-noop`, and `adapter-noop` selected.

| Variant | Direct Python (ns/bar) | Adapter (ns/bar) | Decision |
| --- | ---: | ---: | --- |
| Reuse method-name string | 167.3 | 214.8 | Keep |
| Also cache the bound callable once | 141.1 | 230.7 | Reject |
| Reuse initialization Bar objects, dynamic callable | 129.3 | 181.5 | Reject |

The retained change uses [PyO3's `intern!` macro](https://docs.rs/pyo3/0.28.3/pyo3/macro.intern.html)
to reuse the Python string `"next"` instead of constructing it for every call.
It still resolves the method on every bar.

Caching the bound callable broke
`test_direct_engine_resolves_callback_replacements_on_later_bars`: the old
callback ran three times instead of honoring the replacement on later bars.
The adapter measurement also did not show a consistent gain over the interned
name alone. Reusing initialization Bars measured lower callback cost, but broke
`test_callbacks_keep_distinct_bar_objects_and_retained_values` because the
callback received the same object as the initialization history. These changes
were removed. Fresh callback objects and existing position snapshot semantics
remain intact.

To reproduce the old dispatch in a separate checkout, keep the benchmark hook
and replace the interned-name call in `Engine::run_py` with:

```rust
strategy.bind(py).call_method(
    "next", (bar.clone(), broker_py.clone_ref(py)), None,
)?;
```

Remove the unused `next_name` declaration, rebuild the installed package, and
run the same benchmark command with `--label before`. Do not compare a debug
build with a release build.

## Choose an execution path

Use Python `next()` for decisions that depend on the broker: fills, current
equity, trailing stops, or partial closes. Read `has_positions` or `position_count`
when you only need presence or a count; use `positions` when you need snapshots.

Precompute indicators during initialization when they depend only on market
data, then read the current value during callbacks. The indicator rows include
the added initialization work and show its separate cost.

For broker-independent target-lot signals and parameter grids, use `optimize()`.
It computes one signal array per combination and runs the simulations in native
threads with the GIL released. Python callbacks still execute once per bar under
the GIL. No batching, callback removal, or new single-run signal API is introduced
by this change.
