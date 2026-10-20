# Batch SL/TP benchmark

Run `cargo run --release --example benchmark_sl_tp` from the repository root.
The example opens positions before timing, then measures one `check_sl_tp`
call with all stops triggered. Each of nine repetitions uses a fresh broker;
results are medians. Assertions check that every position closes and trade
order matches submission order. Trade allocation and settlement are included.

Measured on an Intel Core i5-8350U (4 cores / 8 threads), Linux x86_64,
Rust 1.98.1, PyO3 0.28.3, CPython 3.14.4, default Cargo release profile.
The benchmark itself executes entirely in Rust on one thread.
Before uses the repeated `Vec::remove` implementation at commit `867eee6`;
after uses stable in-place compaction. Both use the same example and settings.

| Positions | Before (microseconds) | After (microseconds) |
| ---: | ---: | ---: |
| 1,000 | 521.215 | 7.622 |
| 2,000 | 2,245.135 | 54.041 |
| 4,000 | 10,763.030 | 109.674 |
| 8,000 | 52,533.606 | 222.399 |
| 16,000 | 232,879.797 | 484.124 |
| 32,000 | 966,525.622 | 980.658 |

From 2,000 through 32,000 positions, the new implementation takes roughly
27–31 ns per position, consistent with linear scaling. The 1,000-position
case fits a smaller working set and is faster per position. At 32,000 positions
the measured batch-close speedup is about 986x. These are local microbenchmark
results, not an end-to-end backtest speedup; strategies with few open positions
will see much smaller gains. Timing varies with hardware and system load.
