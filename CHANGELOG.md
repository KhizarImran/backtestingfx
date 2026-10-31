# Changelog

## [Unreleased]

### Added
- Constant-time Python `Strategy` and `Broker` properties `has_positions` and
  `position_count`, avoiding position-list copies for presence and count checks.
- A callback benchmark with separate conversion, engine construction,
  initialization, and simulation timings, native and Python no-op baselines,
  indicator and position-access workloads, correctness checks, and peak RSS.
- Documented and regression-tested the stop-first intrabar SL/TP policy,
  inclusive touches and next-bar checks for new entries.
- Contributor setup and validation guidance, bug/feature issue templates, an
  API/platform compatibility policy, and a prioritized FX roadmap.

### Fixed
- Python strategy dispatch reuses the Python method-name string for `next`,
  while preserving per-bar method lookup, distinct Bar objects, and exceptions.
- The existing cross-library comparison and benchmark now use matched fixed-unit
  next-open strategies, verify trades and full equity curves, and report setup,
  simulation, and total timings separately. Spread is zero; commission is matched.
- Python constructors reject nonfinite or out-of-range broker settings. Order,
  partial-close, stop-update, closing-price, and equity-price inputs are checked
  before account mutation. Finite nonpositive partial sizes remain no-ops.
- Optimization rejects malformed or nonfinite signals, including direct
  `run_grid()` calls. Native SMA calls reject invalid sizes and windows before
  simulation rather than risking a Rust panic.
- SL/TP processing checks the midpoint open first: crossed stops fill at the
  adverse open, while crossed targets receive the favorable open, before any
  later range touches. Exit spread, commission, and conversion still apply.
  Bars that open inside both levels retain stop-first intrabar behavior.
- Batch stop-loss/take-profit processing uses a single linear pass, preserving
  survivor and trade order, fills, and accounting when many positions exit together.
- Native engine runs reset cash, positions, trade history, and position IDs before
  each independent simulation, including repeated `run_native_sma()` calls.
- `spread` now means the full bid–ask width around midpoint OHLC prices. Each
  fill applies half the width, so a one-pip EURUSD spread costs $10 per standard
  lot on a flat round trip, before commission, instead of $20.
- Open equity uses the bid/ask price available to close each position and includes
  the exit-side spread (future exit commission is still charged only at closing).

### Migration
- Python cash, contract size, conversion rate, opening sizes, prices, and supplied
  SL/TP levels must be positive and finite; spread and commission must be finite
  and nonnegative. Invalid numeric inputs now raise `ValueError`. Signal arrays
  must be one-dimensional, finite, and contain one target per bar. See README
  numeric input validation for partial-close and native SMA rules.
- Gap-bar SL/TP fills now use the opening price instead of the configured level.
  An opening target also takes precedence over a later stop touch. Rerun affected
  backtests: PnL, equity, and strategy decisions can change; there is no legacy
  gap-fill option. See README execution policy for long/short examples.
- Double the previous `spread` input to preserve pre-change fills and completed
  trade PnL. Equity curves can still differ because marking now includes exit
  spread. SL/TP levels remain midpoint levels. See README price conventions.

## [0.2.0] - 2026-08-30

First release to reach PyPI since 0.1.1 — the `v0.1.2` tag predated the release
workflow, so 0.1.2 was tagged but never published. Everything listed under 0.1.2
below ships here too.

### Added
- `Strategy.update_sl(id, stop_loss)` — move the stop on an open position in place,
  so a trailing stop doesn't pay spread and commission to close and reopen
- `Strategy.close_partial(id, lot_size)` — scale out of a position, leaving the
  remainder open under the same id

### Changed
- `Broker` closes positions through one internal `settle` helper instead of four
  copies of the same PnL and commission arithmetic

## [0.1.2] - 2026-08-13

### Added
- `Backtest.optimize()` — parallel grid search over a vectorised signal function.
  Simulations run on native threads with the GIL released (`src/optimise.rs`);
  157x faster than looping `run()` over the same grid.
- `strategy_class` is now optional, so `Backtest(df, cash=...)` works for optimization
- Standalone HTML reports (`Backtest.plot()`) and `examples/html_report.py`
- Three-way speed benchmark against backtesting.py (`examples/benchmark.py`)
- Sample EURUSD hourly data is now in the repo, so the examples run on a fresh clone

### Fixed
- Data access inside `next()` is O(1) per bar instead of O(n) — ~2.6x faster

### Changed
- `numpy` is now a direct dependency (it was already pulled in by pandas)
- CI runs `cargo clippy -- -D warnings` in place of `cargo check`

## [0.1.1] - 2026-07-05

### Added
- `self.data`, `self.index`, `self.cash`, `self.equity` properties on Strategy
- Sharpe ratio in Stats output (unannualized)
- `__repr__` on Position — readable output when printing positions
- DataFrame column validation with clear error message

### Fixed
- Trade PnL in history now stores net PnL (after exit commission) — per-trade stats were slightly optimistic
- `Position` pyclass uses `from_py_object` to fix PyO3 deprecation warning
- Removed dead `AttributeError` swallow in engine.rs

### Examples
- Added `examples/sma_cross.py` — SMA 10/50 crossover on EURUSD hourly data
- Added `examples/compare_bt.py` — side-by-side comparison against backtesting.py

## [0.1.0] - 2026-06-21

### Added
- Event-driven backtesting engine on OHLCV bar data
- Simulated broker with buy, sell, close_all, close_position
- Per-position stop loss and take profit
- Realistic FX lot sizing (0.01 / 0.10 / 1.00) with contract_size and quote_to_account conversion
- Full trade history with PnL per trade
- Stats: return, win rate, avg PnL, best/worst trade, profit factor, max drawdown
- Python API — inherit Strategy, run Backtest
- PyO3 Rust extension with Python wrapper
