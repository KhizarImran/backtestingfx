# Changelog

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
