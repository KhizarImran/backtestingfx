# backtestingfx

[![PyPI](https://img.shields.io/pypi/v/backtestingfx)](https://pypi.org/project/backtestingfx/)

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

### Interactive HTML report

Install the optional report dependency and generate a self-contained HTML file:

```bash
pip install "backtestingfx[report]"
```

```python
bt = Backtest(df, MyCrossStrategy, cash=10000.0, spread=0.0001)
stats = bt.run()
bt.plot("strategy-report.html")
```

The report includes candlesticks, trade entries and exits, equity, drawdown,
trade diagnostics, and a complete trade ledger. Plotly is embedded in the file,
so the report works offline without a server.

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
| `self.close_partial(id, lot_size)` | Close part of a position, leaving the rest open |
| `self.update_sl(id, stop_loss)` | Move a position's stop loss (returns `False` if the id is gone) |

### Trailing stops and scaling out

`update_sl` moves the stop on an open position in place, so a trailing stop costs
nothing — closing and reopening would pay spread and commission again. `close_partial`
banks part of a position and leaves the remainder running under the same id, so you can
keep trailing it.

```python
class TrailingBreakout(Strategy):
    def next(self):
        if not self.positions:
            self.buy(1.0, stop_loss=self._bar.close - 0.0050)
            return

        position = self.positions[0]

        # take half off once the trade is 50 pips up
        if self._bar.close > position.entry_price + 0.0050 and position.lot_size > 0.5:
            self.close_partial(position.id, 0.5)

        # trail the stop 50 pips behind price, never backwards
        trail = self._bar.close - 0.0050
        if position.stop_loss is None or trail > position.stop_loss:
            self.update_sl(position.id, trail)
```

`update_sl` does not check which way the stop moves — widening a stop is a legitimate
thing to do, so that call is yours to make (the `trail > position.stop_loss` line above).
`close_partial` with a size at or above the position just closes it outright, and ignores
zero or negative sizes.

### Bar fields

```python
self._bar.open
self._bar.high
self._bar.low
self._bar.close
self._bar.volume
self._bar.timestamp  # unix timestamp (int)
```

## Optimization

Grid-search parameters with the simulations running in parallel Rust threads:

```python
import numpy as np
from backtestingfx import Backtest

def sma_cross(df, fast, slow):
    fast_sma = df["close"].rolling(fast).mean()
    slow_sma = df["close"].rolling(slow).mean()
    return np.where(fast_sma > slow_sma, 0.1, 0.0)   # target lots per bar

bt = Backtest(df, cash=10_000, commission=3.5)
results = bt.optimize(sma_cross, maximize="total_return_pct",
                      fast=range(5, 26), slow=range(30, 101, 5))

best_params, best_stats = results[0]
```

`optimize()` returns `[(params, stats), ...]` sorted best-first by the named `Stats`
field. Re-sort it yourself to minimise something instead.

### Why a signal function instead of `next()`

`next()` runs in Python, so every bar needs the GIL and threads can't help. A signal
function is called **once per parameter combination**, not once per bar — it returns the
target lot size for each bar (positive long, negative short, `0.0` flat), and Rust runs
every simulation natively with the GIL released. On a 315-combination grid that is
**157x faster** than looping `Backtest.run()` over the same grid.

The trade-off: a signal function can't see the broker, so path-dependent logic (trailing
stops, pyramiding, "exit after N bars") still needs `next()` and a plain loop. Indicator
warmup must come out as `0.0`, not `NaN` — a `NaN` signal is rejected rather than
silently treated as "hold".

## Backtest Parameters

```python
Backtest(
    df,                       # pandas DataFrame with OHLCV columns
    StrategyClass,
    cash=10000.0,             # starting account balance in USD
    commission=0.0,           # commission per lot (e.g. 7.0 = $7/lot)
    spread=0.0,               # full bid–ask width in price units (0.0001 = 1 EURUSD pip)
    contract_size=100000.0,   # standard FX lot size, don't change this
    quote_to_account=1.0,     # conversion rate from quote currency to USD
)
```

### Spread and price conventions

OHLC prices are **midpoints** between bid and ask. `spread` is the constant full
bid–ask width in absolute price units, not a percentage or a one-sided offset:

- Bid = midpoint − spread / 2; ask = midpoint + spread / 2.
- Longs enter at ask and exit at bid; shorts enter at bid and exit at ask.
- Open-position equity marks longs at bid and shorts at ask. Entry commission is
  already deducted from cash; future exit commission is excluded until closing.
- Stop-loss and take-profit levels are midpoint levels, triggered by midpoint
  OHLC highs/lows. Their exits use bid for longs and ask for shorts at that level.

For example, with a flat EURUSD midpoint of `1.1000` and `spread=0.0001`, bid is
`1.09995` and ask is `1.10005`. A one-lot long or short loses `$10` from spread
on a round trip (`0.0001 × 100000`), before commission. Its open equity also
reflects that `$10` spread cost. For USDJPY, one pip is typically `0.01` price
units; spread inputs use the instrument's price units.

**Migration from 0.2.0 and earlier:** those versions applied the entire `spread`
value on each side, charging twice the documented bid–ask width. To preserve
previous entry/exit prices and completed-trade PnL, pass twice your old value
(for example, change `0.0001` to `0.0002`). Open equity now includes the exit-side
spread, so historical equity curves and equity-based decisions can still change.
Supply midpoint data: convert bid-only or ask-only data before running a backtest.

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
| `sharpe_ratio` | Unannualized Sharpe ratio |
| `equity_curve` | Account equity from initial cash through final liquidation |
| `trades` | Completed trades with entry, exit, size, direction, and net PnL |

## Data Format

Pass a pandas DataFrame with these columns:

```
open, high, low, close, volume
```

The index should be a `DatetimeIndex`, or include a `timestamp` column. Volume is optional (defaults to 0).

### Reusing a native engine

Native Rust `Engine::run()` and Python `Engine.run_native_sma()` start each run
with fresh broker state using the current initial cash and cost configuration.
Cash, positions, completed trades, position IDs, and equity history do not carry
between runs. For native strategies with mutable internal state, reset that state
in `Strategy::init()` or construct a fresh strategy for each run.

## Why Rust?

The backtesting engine is written in Rust and compiled as a native Python extension via [PyO3](https://pyo3.rs). This means the event loop, broker simulation, and stats computation run at native speed while your strategy stays in plain Python.

## Contributing and project direction

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, checks, and examples,
[COMPATIBILITY.md](COMPATIBILITY.md) for API and platform expectations, and
[ROADMAP.md](ROADMAP.md) for the FX scope and prioritized backlog.
Use the GitHub issue templates for bug reports and feature proposals.

## License

MIT — see [LICENSE](LICENSE)
