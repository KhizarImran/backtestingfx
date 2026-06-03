# backtestingfx

A Rust library for backtesting FX trading strategies on OHLCV data.

Inspired by [backtesting.py](https://kernc.github.io/backtesting.py/), built specifically for forex — lot sizes, long/short positions, stop loss, take profit, and pip-based PnL.

## Overview

Implement the `Strategy` trait, point it at your OHLCV data, and the engine handles the rest. The library is event-driven — your strategy receives one bar at a time and interacts with a simulated broker to open and close positions.

```rust
struct SmaCross {
    period: usize,
    prices: Vec<f64>,
}

impl Strategy for SmaCross {
    fn next(&mut self, bar: &Bar, broker: &mut Broker) {
        self.prices.push(bar.close);

        if self.prices.len() < self.period {
            return;
        }

        let sma: f64 = self.prices.iter().rev().take(self.period).sum::<f64>() / self.period as f64;

        if bar.close > sma {
            broker.buy(bar.close, 1.0, bar.timestamp, None, None);
        } else {
            broker.close_all(bar.close, bar.timestamp);
        }
    }
}
```

## Features

- Event-driven backtesting on OHLCV bar data
- Long and short positions
- Per-position stop loss and take profit
- Full trade history with PnL per trade
- CSV data loading
- Designed to be imported into Python via PyO3 (coming soon)

## Getting Started

Add to your `Cargo.toml`:

```toml
[dependencies]
backtestingfx = { git = "https://github.com/KhizarImran/backtestingfx" }
```

## Project Structure

```
src/
├── lib.rs        # crate root
├── types.rs      # Bar, Position, Trade
├── strategy.rs   # Strategy trait
├── broker.rs     # simulated broker
├── engine.rs     # backtest event loop
└── data.rs       # CSV data loader
```

## Status

Early development. Core types and broker are implemented. Data loading and engine are in progress.

## License

MIT — see [LICENSE](LICENSE)
