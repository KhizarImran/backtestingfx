# backtestingfx

[![PyPI](https://img.shields.io/pypi/v/backtestingfx)](https://pypi.org/project/backtestingfx/)

A Rust-powered FX backtesting library for Python. Write your strategy in Python, let Rust handle the heavy lifting.

Inspired by [backtesting.py](https://kernc.github.io/backtesting.py/) but built specifically for forex — lot sizes, pip-based PnL, stop loss, take profit, and realistic account currency conversion.

This README describes `main`, including changes listed as
[Unreleased](CHANGELOG.md). See [Installation](#installation) to build the
current checkout; an installed release can have fewer features.

```
pip install backtestingfx
```

## Quick Start

From the repository root, use the bundled EURUSD sample:

```python
import pandas as pd
from backtestingfx import Backtest, Strategy

class MyCrossStrategy(Strategy):
    def next(self):
        self.close_all()
        self.buy(lot_size=0.1)

df = pd.read_csv("data/EURUSD_1H.csv")

bt = Backtest(df, MyCrossStrategy, cash=10000.0, spread=0.0001)
stats = bt.run()

print(stats)
```

```
--- Backtest Results ---
Initial Cash:   10000.00
Final Cash:     7669.90
Total Return:   -23.30%
Trades:         2000
Win Rate:       39.8%
Avg PnL:        -1.16505
Best Trade:     33.10000
Worst Trade:    -50.50000
Profit Factor:  0.62
Max Drawdown:   23.38%
Swap Cash Flow: 0.00
Sharpe Ratio:   -0.1652 (unannualized)
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
trade diagnostics, and a complete trade ledger. With swaps enabled, it also
shows financing totals, calendar assumptions, and dated postings. Plotly is embedded in the file,
so the report works offline without a server.

## Installation

```
pip install backtestingfx
```

Requires Python 3.9+.

To use the current source, clone this repository and install from its root:

```bash
git clone https://github.com/KhizarImran/backtestingfx.git
cd backtestingfx
python -m pip install .
```

A source build requires Rust and a native compiler. Use `".[report]"` instead
of `.` to include HTML reporting. See [CONTRIBUTING.md](CONTRIBUTING.md) for
development setup.

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

### Position inspection

Use `self.has_positions` to check whether any positions are open, or
`self.position_count` to read their count. Both read the current broker state
in constant time without copying positions. Before a run, they return `False`
and `0`. The direct Python `Broker` API provides the same read-only properties.

`self.positions` still returns a list of position snapshots. Use it when you
need IDs, sizes, or stop levels. For example:

```python
if not self.has_positions:
    self.buy(0.1)
```

### Trailing stops and scaling out

`update_sl` moves the stop on an open position in place, so a trailing stop costs
nothing — closing and reopening would pay spread and commission again. `close_partial`
banks part of a position and leaves the remainder running under the same id, so you can
keep trailing it.

```python
class TrailingBreakout(Strategy):
    def next(self):
        if not self.has_positions:
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
every simulation natively with the GIL released. Earlier documentation reported
a 157x speedup on a 315-combination grid. That historical measurement predates
the ingestion improvements and is not a current performance guarantee.
Optimization timing, memory, and grid-size measurements remain tracked in
[#39](https://github.com/KhizarImran/backtestingfx/issues/39).

The trade-off: a signal function can't see the broker, so path-dependent logic (trailing
stops, pyramiding, "exit after N bars") still needs `next()` and a plain loop. Indicator
warmup must come out as `0.0`, not `NaN` — a `NaN` signal is rejected rather than
silently treated as "hold".

Precompute indicators once in `init()` when their values depend only on market
data, then read the current value in `next()`. Python callbacks remain useful
for decisions that depend on fills, cash, trailing stops, or partial closes.
The [callback benchmark](examples/benchmark_callbacks.md) separates conversion,
initialization, and simulation costs and compares these workloads.

## Backtest Parameters

Optional [swaps and overnight financing](SWAPS.md) use a `SwapConfig` passed as
`Backtest(df, StrategyClass, swap=SwapConfig(long=-7.0, short=2.0))`. Rates are
signed account-currency cash per lot, with configurable local rollover time,
timezone, weekday multipliers, and holiday overrides. Swaps are disabled by
default. Import `SwapConfig` from `backtestingfx`; see `examples/swaps.py`.

```python
Backtest(
    df,                       # pandas DataFrame with OHLCV columns
    StrategyClass,
    cash=10000.0,             # starting account balance in USD
    commission=0.0,           # account-currency commission per lot per side
    spread=0.0,               # full bid–ask width in price units (0.0001 = 1 EURUSD pip)
    contract_size=100000.0,   # standard FX lot size, don't change this
    quote_to_account=1.0,     # conversion rate from quote currency to USD
    swap=None,                # optional SwapConfig; disabled by default
)
```

### Numeric input validation

`Backtest`, `Broker`, and `Engine` constructors reject invalid numeric settings
with `ValueError` before simulation:

| Input | Allowed values |
| --- | --- |
| `cash` (`initial_cash` on `Broker` and `Engine`) | Finite and greater than zero |
| `commission`, `spread` | Finite and nonnegative |
| `contract_size`, `quote_to_account` | Finite and greater than zero |

Python `buy()` and `sell()` require positive finite lot sizes and prices.
Supplied stop-loss and take-profit levels, including `update_sl()` values, must
also be positive and finite. Levels are not restricted relative to the entry
price: a trailing stop can lock in profit, and stops can still be widened.
Closing prices and equity mark prices must be positive and finite.
Rejected calls leave cash, positions, and position IDs unchanged.

`close_partial()` rejects nonfinite sizes. Finite zero or negative sizes remain
no-ops, and a size at or above the remaining position closes it completely.
Unknown position IDs remain no-ops; `update_sl()` returns `False` for them.
These behaviors require otherwise valid numeric arguments.

`optimize()` requires each signal to be a one-dimensional numeric array with
one finite target lot size per bar. Positive targets mean long, negative targets
mean short, and zero means flat. Use zero during indicator warmup. Both NaN and
infinity are rejected. Direct extension `run_grid()` calls also check every
signal's length and finiteness before starting worker threads.
`Engine.run_native_sma()` requires a positive finite lot size and integer
windows satisfying `1 <= fast <= slow`.

These checks apply at Python entry points, including direct extension calls.
Native Rust callers remain responsible for supplying valid inputs to the
simulation methods. [Market-data validation](MARKET_DATA.md) requires positive
finite OHLC values, consistent candle ranges, nonnegative finite volume, and
strictly increasing whole-second timestamps before simulation. Empty data is
valid. Numeric timestamps require an explicit `timestamp_unit`; naive datetimes
mean UTC, and all timestamps floor to whole seconds. See
[timestamp rules](MARKET_DATA.md#timestamp-sources-and-ordering).

**Migration:** replace invalid inputs that previously passed through, including
zero initial cash, nonpositive opening sizes, and infinite signals. Calls now
raise an error instead of silently producing invalid account state.

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

### Intrabar stop-loss and take-profit policy

The broker checks the midpoint **open first** for existing positions:

- A long stop triggers when open <= SL; a short stop triggers when open >= SL.
  It fills at the open, accepting the worse price if the market gaps past the stop.
- A long target triggers when open >= TP; a short target triggers when open <= TP.
  It fills at the open, giving price improvement if the market gaps past the target.
- An opening fill takes precedence over any later high/low touch of the other
  level. Exact touches at the open count as hits.

If neither level triggers at the open, stops and targets fill at their configured
midpoint levels when touched by the bar's range. OHLC bars do not reveal the order
of those later touches. If both are touched, the broker chooses the
**stop-loss first**, for both long and short positions. Exact touches count as
hits. This conservative assumption can differ from the outcome on tick data;
it does not reconstruct the intrabar price path. All fills apply exit-side spread
(bid for longs, ask for shorts) and exit commission using the usual cost model.

For example, a long entered at midpoint `1.1000`, with SL `1.0900` and TP
`1.1100`, exits at the stop midpoint when the next bar opens at `1.1000` and
has low `1.0800` and high `1.1200`. Bid/ask spread and exit commission still
apply to the fill.

The engine checks existing positions' SL/TP **before** calling the strategy for
each bar. Orders opened by `next()` fill at that bar's close; their SL/TP levels
are first checked on the following bar. The entry bar's earlier high/low is not
replayed against the new position. A position opened on the final bar instead
undergoes the usual final liquidation at that bar's close.

For a gap example, a long with SL `1.0950` exits at midpoint `1.0900` if the
next bar opens there. A long with TP `1.1100` exits at midpoint `1.1200` if the
next bar opens there, even if that bar later falls through the stop. A short
uses the opposite inequalities. This policy assumes execution at the opening
quote without additional slippage or liquidity constraints; it has no
configurable intrabar ordering option.

**Migration:** previous versions always used the configured SL/TP level and
chose the stop whenever the range touched both levels, regardless of the open.
Gap-bar fills, PnL, equity, and later strategy decisions can therefore change.
Rerun affected backtests; there is no setting to restore the old gap behavior.

### Trading non-USD pairs

`quote_to_account` converts the pair's quote currency into the account currency.
The rate is constant over a run. For a USD account, `1.0` applies to USD-quoted
pairs such as EURUSD and GBPUSD.

For other pairs with a USD account, example inputs are:

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

Optional [margin and liquidation](MARGINS.md) checks use
`Backtest(df, MyStrategy, margin=MarginConfig(leverage=30))`. Import `MarginConfig`
from `backtestingfx`. Margin is disabled by default. With checks enabled, orders
can be rejected and a breached account stops trading after full-book liquidation.
Strategy `buy()` and `sell()` return an `OrderResult`; inspect `accepted`,
`position_id`, and `reason`. Strategy `used_margin` and `free_margin` use the
current closing price. See the guide for gross hedges, costs, observed-price
checks, and the [hand-calculated example](examples/margin.py).

| Field | Description |
|-------|-------------|
| `initial_cash` | Starting balance |
| `final_cash` | Ending balance |
| `total_return_pct` | Total return as a percentage |
| `num_trades` | Number of completed exit records; each partial close counts separately |
| `num_wins` | Number of winning trades |
| `win_rate_pct` | Win rate as a percentage |
| `avg_pnl` | Average net PnL per exit record in account currency |
| `best_trade` | Best single exit's net PnL in account currency |
| `worst_trade` | Worst single exit's net PnL in account currency |
| `profit_factor` | Gross profit / gross loss |
| `max_drawdown_pct` | Maximum drawdown as a percentage |
| `sharpe_ratio` | Unannualized Sharpe ratio |
| `equity_curve` | Account equity from initial cash through final liquidation |
| `trades` | Completed exit records with entry, exit, size, direction, allocated swap, and net PnL |
| `total_swap` | Signed financing posted to cash; zero when swaps are disabled |
| `swap_charges` | Dated financing records with position ID, lots, direction, multiplier, and signed amount |
| `margin` | Immutable margin configuration, or `None` for unlimited buying power |
| `account_status` | `unrestricted`, `active`, or `liquidated` |
| `order_rejections` | Dated rejected orders, reasons, and prospective account snapshots |
| `liquidation` | Optional stopout evidence, including trigger, positions closed, and settled cash |

Net trade PnL includes spread through fill prices, both commission legs, and
allocated `trade.swap`. Negative swap values are charges; positive values are
credits. A position snapshot's `swap` contains financing accrued on its remaining
lots. See [SWAPS.md](SWAPS.md) for allocation and cash reconciliation.

## Data Format

Pass a pandas DataFrame with these columns:

```
open, high, low, close, volume
```

Use a `DatetimeIndex` or include a `timestamp` column. OHLCV and timestamp column
names are case-insensitive. Volume is optional and defaults to zero. Market data
requires positive finite OHLC, consistent candle ranges, nonnegative finite
volume, and strictly increasing whole-second timestamps. Enabled swaps add
rollover-calendar range checks. Numeric timestamp columns require
`Backtest(df, MyStrategy, timestamp_unit="s")`, using `"s"`, `"ms"`, `"us"`, or
`"ns"` to match the input. Naive datetimes mean UTC; aware values normalize to
UTC. Fractions floor to whole seconds. Localize local naive data explicitly
before passing it to the backtest.

Data is checked before strategy or signal callbacks. See
[MARKET_DATA.md](MARKET_DATA.md) for ordering, invalid-input errors, empty-input
behavior, and the native CSV loader's fallible API.

### Reusing a native engine

Native Rust `Engine::run()` and Python `Engine.run_native_sma()` start each run
with fresh broker state using the current initial cash and cost configuration.
Cash, positions, completed trades, position IDs, financing, margin events, and equity history do not carry
between runs. For native strategies with mutable internal state, reset that state
in `Strategy::init()` or construct a fresh strategy for each run.

Native `Engine::run()` and `run_with_swap()` return `Result<Stats, SimulationError>`.
Handle failures with `?` or a match. `run_with_swap()` enables optional financing.
Python `Engine(..., swap=config)` applies its configuration to Python
callbacks and `run_native_sma()`. See [native swap execution](SWAPS.md#native-execution-and-optimization).

## Why Rust?

The backtesting engine is written in Rust and compiled as a native Python extension via [PyO3](https://pyo3.rs). This means the event loop, broker simulation, and stats computation run at native speed while your strategy stays in plain Python.

The [matched backtesting.py comparison](examples/benchmark.md) checks trades and
full equity curves before timing both libraries. After bulk data ingestion,
the [setup benchmark](examples/benchmark_ingestion.md) measures total execution
about 48–50 times faster than the previous implementation, and 5.5–5.7 times
faster than backtesting.py on matched 50,000- and 100,000-bar SMA runs. These local results
include setup and statistics; speed depends on the data and strategy.

## Contributing and project direction

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, checks, and examples,
[COMPATIBILITY.md](COMPATIBILITY.md) for API and platform expectations, and
[ROADMAP.md](ROADMAP.md) for the FX scope and prioritized backlog.
Use the GitHub issue templates for bug reports and feature proposals.

## License

MIT — see [LICENSE](LICENSE)
