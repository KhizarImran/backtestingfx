import itertools
from typing import Any

from backtestingfx import _backtestingfx as _rust  # type: ignore
import numpy as np
import pandas as pd


class _DataView:
    # ponytail: a window over the full bars list, visible up to the current bar.
    # Slicing copies only the requested slice, not the growing prefix — this is
    # what keeps data access O(1) per bar instead of O(n) (was O(n^2) overall).
    __slots__ = ("_bars", "_len")

    def __init__(self, bars):
        self._bars = bars
        self._len = 0

    def __len__(self):
        return self._len

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self._bars[k] for k in range(*i.indices(self._len))]
        if i < 0:
            i += self._len
        if not 0 <= i < self._len:
            raise IndexError("bar index out of range")
        return self._bars[i]

    def __iter__(self):
        return (self._bars[k] for k in range(self._len))


class Strategy:
    def __init__(self):
        self._bars: Any = None
        self._bar: Any = None
        self._broker: Any = None
        self._index: int = 0
        self._data_view: Any = None

    @property
    def positions(self):
        return self._broker.positions() if self._broker else []

    @property
    def data(self):
        if not self._bars:
            return []
        if self._data_view is None:
            self._data_view = _DataView(self._bars)
        self._data_view._len = self._index + 1
        return self._data_view

    @property
    def index(self):
        return self._index

    @property
    def cash(self):
        return self._broker.cash if self._broker else 0.0

    @property
    def equity(self):
        return self._broker.equity(self._bar.close) if self._broker else 0.0

    def init(self):
        pass

    def next(self):
        pass

    def buy(self, lot_size, stop_loss=None, take_profit=None):
        self._broker.buy(
            self._bar.close, lot_size, self._bar.timestamp, stop_loss, take_profit
        )

    def sell(self, lot_size, stop_loss=None, take_profit=None):
        self._broker.sell(
            self._bar.close, lot_size, self._bar.timestamp, stop_loss, take_profit
        )

    def close_all(self):
        self._broker.close_all(self._bar.close, self._bar.timestamp)

    def close_position(self, id):
        self._broker.close_position(id, self._bar.close, self._bar.timestamp)


class _Adapter:
    def __init__(self, strategy):
        self._strategy = strategy
        self._index = 0

    def init(self, bars):
        self._strategy._bars = bars
        self._strategy.init()

    def next(self, bar, broker):
        self._strategy._bar = bar
        self._strategy._broker = broker
        self._strategy._index = self._index
        self._index += 1
        self._strategy.next()


class Backtest:
    def __init__(
        self,
        df,
        strategy_class=None,  # optional: optimize() uses a signal function instead
        cash=10000.0,
        commission=0.0,
        spread=0.0,
        contract_size=100000.0,
        quote_to_account=1.0,
    ):
        self._df = df
        self._strategy_class = strategy_class
        self._cash = cash
        self._commission = commission
        self._spread = spread
        self._contract_size = contract_size
        self._quote_to_account = quote_to_account
        self._stats = None
        self._report_df = None

    def _to_bars(self, df):
        required = {"open", "high", "low", "close"}
        missing = required - set(df.columns.str.lower())
        if missing:
            raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

        bars = []
        for idx, row in df.iterrows():
            if isinstance(idx, pd.Timestamp):
                ts = int(idx.timestamp())
            else:
                ts = int(pd.Timestamp(row["timestamp"]).timestamp())  # type: ignore

            bars.append(
                _rust.Bar(  # type: ignore
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                )
            )
        return bars

    def _engine(self, df):
        return _rust.Engine(  # type: ignore
            self._to_bars(df),
            self._cash,
            self._commission,
            self._spread,
            self._contract_size,
            self._quote_to_account,
        )

    def run(self):
        if self._strategy_class is None:
            raise ValueError("Backtest needs a strategy_class to run(); use optimize() for signal functions")

        self._stats = None
        self._report_df = None
        report_df = self._df.copy(deep=True)
        engine = self._engine(report_df)
        strategy = self._strategy_class()
        self._stats = engine.run(_Adapter(strategy))
        self._report_df = report_df
        return self._stats

    def optimize(self, signal_fn, maximize="total_return_pct", **grid):
        """Grid-search `signal_fn` over the given parameter ranges. Best result first.

        `signal_fn(df, **params)` is called once per combination and returns one target
        lot size per bar: positive for long, negative for short, 0.0 for flat. Write it
        vectorised (pandas/numpy) — it runs in Python, but only once per combination,
        never per bar. The simulations themselves run in parallel Rust threads.

        Returns a list of `(params, stats)` sorted by the named Stats field, so
        `results[0]` is the best run. Sort it yourself to minimise something instead.
        """
        if not grid:
            raise ValueError("optimize needs at least one parameter range")

        names = list(grid)
        combos = [dict(zip(names, values)) for values in itertools.product(*grid.values())]

        signals = []
        for combo in combos:
            signal = np.asarray(signal_fn(self._df, **combo), dtype=float)
            if np.isnan(signal).any():
                raise ValueError(
                    f"signal_fn returned NaN for {combo} — indicator warmup should "
                    "produce 0.0 (flat), not NaN"
                )
            # ponytail: tolist() is the cheap bridge into Rust. It costs one Python
            # float per bar per combo; swap in the `numpy` crate for a zero-copy
            # PyReadonlyArray1 if this ever shows up in a profile.
            signals.append(signal.tolist())

        engine = self._engine(self._df)
        results = _rust.run_grid(engine, signals)  # type: ignore

        return sorted(
            zip(combos, results),
            key=lambda pair: getattr(pair[1], maximize),
            reverse=True,
        )

    def plot(self, filename="backtest.html", open_browser=True):
        if self._stats is None:
            raise RuntimeError("Run the backtest before plotting it")

        from backtestingfx.plotting import render_report

        return render_report(
            self._report_df,
            self._stats,
            strategy_name=self._strategy_class.__name__,
            filename=filename,
            open_browser=open_browser,
            commission=self._commission,
            spread=self._spread,
            contract_size=self._contract_size,
            quote_to_account=self._quote_to_account,
        )
