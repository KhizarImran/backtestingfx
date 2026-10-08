# Changelog

## [Unreleased]

### Added
- Contributor setup and validation guidance, bug/feature issue templates, an
  API/platform compatibility policy, and a prioritized FX roadmap.

### Fixed
- Native engine runs reset cash, positions, trade history, and position IDs before
  each independent simulation, including repeated `run_native_sma()` calls.
- `spread` now means the full bid–ask width around midpoint OHLC prices. Each
  fill applies half the width, so a one-pip EURUSD spread costs $10 per standard
  lot on a flat round trip, before commission, instead of $20.
- Open equity uses the bid/ask price available to close each position and includes
  the exit-side spread (future exit commission is still charged only at closing).

### Migration
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
