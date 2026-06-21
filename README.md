# backtestingfx

A Rust-powered FX backtesting library for Python. Write your strategy in Python, let Rust handle the heavy lifting.

Inspired by [backtesting.py](https://kernc.github.io/backtesting.py/) but built specifically for forex — lot sizes, pip-based PnL, stop loss, take profit, and realistic account currency conversion.

```
pip install backtestingfx
```

## Quick Start

```python
import pandas as pd
from backtestingfx import Backtest, Strategy

class MyCrossStrategy(Strategy):
    def next(self):
        self.close_all()
        self.buy(lot_size=0.1)

df = pd.read_csv("EURUSD_1H.csv")

bt = Backtest(df, MyCrossStrategy, cash=10000.0, spread=0.0001)
stats = bt.run()

print(stats)
```

```
--- Backtest Results ---
Initial Cash:   10000.00
Final Cash:     9823.50
Total Return:   -1.77%
Trades:         248
Win Rate:       52.4%
Avg PnL:        -0.71380
Best Trade:     84.20000
Worst Trade:    -61.30000
Profit Factor:  0.94
Max Drawdown:   3.21%
```

## Installation

```
pip install backtestingfx
```

Requires Python 3.9+.

## Writing a Strategy

Inherit from `Strategy` and implement `next()`. It is called once per bar.

```python
from backtestingfx import Backtest, Strategy

class MyStrategy(Strategy):
    def init(self):
        # called once before the loop starts
        # self._bars contains all Bar objects if you need to pre-compute
        pass

    def next(self):
        # self._bar  — current bar (open, high, low, close, volume, timestamp)
        # self._broker — the broker instance (advanced use)

        if self._bar.close > 1.1000:
            self.buy(lot_size=0.1, stop_loss=1.0950, take_profit=1.1100)
        else:
            self.close_all()
```

### Strategy methods

| Method | Description |
|--------|-------------|
| `self.buy(lot_size, stop_loss=None, take_profit=None)` | Open a long position |
| `self.sell(lot_size, stop_loss=None, take_profit=None)` | Open a short position |
| `self.close_all()` | Close all open positions |
| `self.close_position(id)` | Close a specific position by ID |

### Bar fields

```python
self._bar.open
self._bar.high
self._bar.low
self._bar.close
self._bar.volume
self._bar.timestamp  # unix timestamp (int)
```

## Backtest Parameters

```python
Backtest(
    df,                       # pandas DataFrame with OHLCV columns
    StrategyClass,
    cash=10000.0,             # starting account balance in USD
    commission=0.0,           # commission per lot (e.g. 7.0 = $7/lot)
    spread=0.0,               # spread in price units (e.g. 0.0001 = 1 pip)
    contract_size=100000.0,   # standard FX lot size, don't change this
    quote_to_account=1.0,     # conversion rate from quote currency to USD
)
```

### Trading non-USD pairs

By default `quote_to_account=1.0` which is correct for USD-quoted pairs (EURUSD, GBPUSD).

For other pairs, pass the rate that converts the quote currency to USD:

| Pair | quote_to_account |
|------|-----------------|
| EURUSD, GBPUSD | `1.0` (default) |
| EURGBP | GBPUSD rate (e.g. `1.27`) |
| USDCAD, GBPCAD | CADUSD rate (e.g. `0.74`) |
| USDJPY | JPYUSD rate (e.g. `0.0067`) |

```python
bt = Backtest(df, MyStrategy, cash=10000.0, spread=0.00015, quote_to_account=1.27)
```

## Stats

| Field | Description |
|-------|-------------|
| `initial_cash` | Starting balance |
| `final_cash` | Ending balance |
| `total_return_pct` | Total return as a percentage |
| `num_trades` | Number of completed trades |
| `num_wins` | Number of winning trades |
| `win_rate_pct` | Win rate as a percentage |
| `avg_pnl` | Average PnL per trade in USD |
| `best_trade` | Best single trade PnL in USD |
| `worst_trade` | Worst single trade PnL in USD |
| `profit_factor` | Gross profit / gross loss |
| `max_drawdown_pct` | Maximum drawdown as a percentage |

## Data Format

Pass a pandas DataFrame with these columns:

```
open, high, low, close, volume
```

The index should be a `DatetimeIndex`, or include a `timestamp` column. Volume is optional (defaults to 0).

## Why Rust?

The backtesting engine is written in Rust and compiled as a native Python extension via [PyO3](https://pyo3.rs). This means the event loop, broker simulation, and stats computation run at native speed while your strategy stays in plain Python.

## License

MIT — see [LICENSE](LICENSE)
