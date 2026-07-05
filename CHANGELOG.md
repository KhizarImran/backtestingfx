# Changelog

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
