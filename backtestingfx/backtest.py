import itertools
import operator
import warnings
from typing import Any

from backtestingfx import _backtestingfx as _rust  # type: ignore
import numpy as np
import pandas as pd
from backtestingfx._timestamps import UNITS, index_seconds, timestamp_value
from backtestingfx._input import (
    REFERENCE_OHLCV, InputMode, ReferenceSnapshot, prepare_reference, reference_columns,
)


class _DataView:
    # ponytail: a window over the full bars list, visible up to the current bar.
    # Slicing copies only the requested slice, not the growing prefix — this is
    # what keeps data access O(1) per bar instead of O(n) (was O(n^2) overall).
    __slots__ = ("_bars", "_len", "_snapshot")

    def __init__(self, bars, snapshot=None):
        self._bars = bars
        self._len = 0
        self._snapshot = snapshot

    @property
    def index(self):
        if self._snapshot is None:
            raise AttributeError("this data view has no source index")
        return self._snapshot.index[:self._len]

    @property
    def df(self):
        if self._snapshot is None:
            raise AttributeError("this data view has no source frame")
        return self._snapshot.frame.iloc[:self._len]

    def _set_length(self, length):
        self._len = length

    def _bar(self, offset):
        bar = self._bars[offset]
        return bar if self._snapshot is None else self._snapshot.record(bar)

    def __len__(self):
        return self._len

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self._bar(k) for k in range(*i.indices(self._len))]
        if i < 0:
            i += self._len
        if not 0 <= i < self._len:
            raise IndexError("bar index out of range")
        return self._bar(i)

    def __iter__(self):
        return (self._bar(k) for k in range(self._len))


class _StrategyColumn(np.ndarray):
    def __new__(cls, series, index):
        column = np.asarray(series).view(cls)
        column.name = series.name
        column._index = index
        return column

    def __array_finalize__(self, source):
        self.name = getattr(source, "name", None)
        self._index = getattr(source, "_index", None)

    def __bool__(self):
        return bool(self[-1]) if self.ndim and self.shape[0] else super().__bool__()

    def __float__(self):
        return float(self[-1]) if self.ndim and self.shape[0] else super().__float__()

    @property
    def s(self):
        values = np.atleast_2d(np.asarray(self))
        return pd.Series(values[0], index=self._index[:values.shape[1]], name=self.name)

    @property
    def df(self):
        values = np.atleast_2d(np.asarray(self))
        return pd.DataFrame(
            values.T, index=self._index[:values.shape[1]], columns=[self.name] * len(values),
        )


class _ReferenceDataView(_DataView):
    __slots__ = ("_columns", "_visible", "_pip")

    def __init__(self, bars, snapshot):
        super().__init__(bars, snapshot)
        self._columns = {}
        self._visible = {}
        self._pip = None

    def _set_length(self, length):
        super()._set_length(length)
        self._visible.clear()

    def _column(self, name):
        if name not in REFERENCE_OHLCV:
            raise KeyError(name)
        if name not in self._columns:
            self._columns[name] = _StrategyColumn(
                self._snapshot.frame[name], self._snapshot.index,
            )
        return self._columns[name]

    def __getitem__(self, item):
        if not isinstance(item, str):
            return super().__getitem__(item)
        if item not in self._visible:
            self._visible[item] = self._column(item)[:self._len]
        return self._visible[item]

    def __getattr__(self, name):
        if name in REFERENCE_OHLCV:
            return self[name]
        raise AttributeError(f"Column {name!r} not in data")

    @property
    def pip(self):
        if self._pip is None:
            prices = np.asarray(self._column("Close")).astype(str)
            decimals = [len(price.partition(".")[2]) for price in prices]
            self._pip = float(10 ** -np.median(decimals))
        return self._pip


class Strategy:
    def __init__(self):
        self._bars: Any = None
        self._bar: Any = None
        self._broker: Any = None
        self._index: int = 0
        self._data_view: Any = None
        self._snapshot = None
        self._queued_execution = False

    @property
    def positions(self):
        positions = self._broker.positions() if self._broker else []
        return positions if self._snapshot is None else [self._snapshot.record(p) for p in positions]

    @property
    def position_count(self):
        return self._broker.position_count if self._broker is not None else 0

    @property
    def has_positions(self):
        return self._broker.has_positions if self._broker is not None else False

    @property
    def data(self):
        if self._snapshot is not None or self._queued_execution:
            return self._data_view
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

    @property
    def used_margin(self):
        return self._broker.used_margin(self._bar.close) if self._broker is not None else 0.0

    @property
    def free_margin(self):
        return self._broker.free_margin(self._bar.close) if self._broker is not None else 0.0

    def init(self):
        pass

    def next(self):
        pass

    def execution_warmup(self):
        """Number of additional bars to skip after bar zero in queued execution."""
        return 0

    def buy(self, lot_size, stop_loss=None, take_profit=None):
        return self._broker.buy(
            self._bar.close, lot_size, self._bar.timestamp, stop_loss, take_profit
        )

    def sell(self, lot_size, stop_loss=None, take_profit=None):
        return self._broker.sell(
            self._bar.close, lot_size, self._bar.timestamp, stop_loss, take_profit
        )

    def close_all(self):
        self._broker.close_all(self._bar.close, self._bar.timestamp)

    def close_position(self, id):
        self._broker.close_position(id, self._bar.close, self._bar.timestamp)

    def close_partial(self, id, lot_size):
        """Close `lot_size` lots of a position, leaving the rest open."""
        self._broker.close_partial(id, lot_size, self._bar.close, self._bar.timestamp)

    def update_sl(self, id, stop_loss):
        """Move a position's stop loss. Returns False if there is no such position."""
        return self._broker.update_sl(id, stop_loss)


class _Adapter:
    def __init__(self, strategy, snapshot=None):
        self._strategy = strategy
        self._index = 0
        self._snapshot = snapshot

    def init(self, bars):
        self._strategy._bars = bars
        self._strategy._snapshot = self._snapshot
        if self._snapshot is not None:
            self._strategy._data_view = _ReferenceDataView(bars, self._snapshot)
        elif self._strategy._queued_execution:
            self._strategy._data_view = _DataView(bars)
        if self._strategy._data_view is not None:
            self._strategy._data_view._set_length(len(bars))
        self._strategy.init()

    def _init_execution(self, bars, broker):
        self._strategy._queued_execution = True
        self._strategy._broker = broker
        if bars:
            self._strategy._bar = bars[-1]
            self._strategy._index = len(bars) - 1
        self.init(bars)
        warmup = operator.index(self._strategy.execution_warmup())
        if warmup < 0:
            raise ValueError("execution_warmup() must return a nonnegative integer")
        self._index = min(warmup + 1, max(len(bars), 1))
        return self._index

    def next(self, bar, broker):
        self._strategy._bar = bar
        self._strategy._broker = broker
        self._strategy._index = self._index if self._snapshot is None else bar.timestamp
        self._index += 1
        if self._snapshot is not None or self._strategy._queued_execution:
            self._strategy._data_view._set_length(self._strategy._index + 1)
        self._strategy.next()


class Backtest:
    """Backtest midpoint OHLC data with a full bid–ask spread in price units.

    In data_mode='fx' (default), OHLCV and timestamp names are case-insensitive.
    Numeric timestamp columns require timestamp_unit='s', 'ms', 'us', or 'ns'.
    Naive datetimes mean UTC; aware datetimes retain their UTC instant. All
    timestamps floor to whole Unix seconds before ordering checks.

    data_mode='reference' uses exact Open/High/Low/Close names and preserves
    the prepared pandas index, including periods, duplicates and nanoseconds.
    Orders and sizing still use the FX API; calendar swaps require FX mode.

    Passing trade_on_close or finalize_trades enables queued market orders
    and a first callback after bar zero. Both flags default to False in that mode.

    Fills apply half the spread on each side of the midpoint. Open equity marks
    positions at their closing bid/ask, excluding future exit commission.
    """

    def __init__(
        self,
        df,
        strategy_class=None,  # optional: optimize() uses a signal function instead
        cash=10000.0,
        commission=0.0,
        spread=0.0,
        contract_size=100000.0,
        quote_to_account=1.0,
        *,
        swap=None,
        timestamp_unit=None,
        margin=None,
        data_mode="fx",
        trade_on_close=None,
        finalize_trades=None,
    ):
        if not isinstance(df, pd.DataFrame):
            raise TypeError("market data must be a pandas DataFrame")
        _rust._validate_config(cash, commission, spread, contract_size, quote_to_account)
        self._data_mode = InputMode.parse(data_mode)
        for name, flag in (("trade_on_close", trade_on_close), ("finalize_trades", finalize_trades)):
            if flag is not None and not isinstance(flag, bool):
                raise TypeError(f"{name} must be a bool or None")
        self._execution_timing = (
            None if trade_on_close is None and finalize_trades is None
            else (bool(trade_on_close), bool(finalize_trades))
        )
        if self._data_mode is InputMode.REFERENCE and swap is not None:
            raise ValueError("reference input row clocks cannot drive a swap calendar; use data_mode='fx'")
        self._df = prepare_reference(df, cash) if self._data_mode is InputMode.REFERENCE else df
        self._strategy_class = strategy_class
        self._cash = cash
        self._commission = commission
        self._spread = spread
        self._contract_size = contract_size
        self._quote_to_account = quote_to_account
        if swap is not None and not isinstance(swap, _rust.SwapConfig):
            raise TypeError("swap must be a SwapConfig or None")
        self._swap = swap
        if margin is not None and not isinstance(margin, _rust.MarginConfig):
            raise TypeError("margin must be a MarginConfig or None")
        self._margin = margin
        if timestamp_unit is not None and (
            not isinstance(timestamp_unit, str) or timestamp_unit not in UNITS
        ):
            raise ValueError("timestamp_unit must be None, 's', 'ms', 'us', or 'ns'")
        self._timestamp_unit = timestamp_unit
        self._stats = None
        self._report_df = None
        self._report_timestamps = None
        self._report_snapshot = None

    def _bar_columns(self, df):
        if self._data_mode is InputMode.REFERENCE:
            return reference_columns(df)
        required = {"open", "high", "low", "close"}
        columns = {}
        for column in df.columns:
            normalized = column.lower() if isinstance(column, str) else column
            if normalized in columns:
                raise ValueError(
                    f"DataFrame has duplicate columns after normalization: {normalized!r}"
                )
            columns[normalized] = column
        missing = required - columns.keys()
        if missing:
            raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

        if isinstance(df.index, pd.DatetimeIndex) and df.index.hasnans:
            offset = int(np.flatnonzero(df.index.isna())[0])
            raise ValueError(f"bar {offset}: timestamp is missing (NaT)")
        if len(df) and not isinstance(df.index, pd.DatetimeIndex) and "timestamp" not in columns:
            if not all(isinstance(index, pd.Timestamp) for index in df.index):
                raise ValueError("DataFrame needs a DatetimeIndex or timestamp column")

        names = ("open", "high", "low", "close", "volume")
        numeric = all(isinstance(df[columns[name]].dtype, np.dtype)
                      and df[columns[name]].dtype.kind in "biuf"
                      for name in names if name in columns)
        if not numeric or not isinstance(df.index, pd.DatetimeIndex) or df.index.hasnans:
            return self._scalar_bar_columns(df, columns)

        timestamps = index_seconds(df.index)
        values = []
        for name in names:
            if name not in columns:
                values.append(np.zeros(len(df), dtype=np.float64))
                continue
            values.append(np.require(df[columns[name]].to_numpy(), dtype=np.float64,
                                     requirements=["C", "A"]))
        return timestamps, values

    @staticmethod
    def _float_value(value, field, offset):
        try:
            if np.iscomplexobj(value):
                raise ValueError("complex value")
            return float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"bar {offset}: {field} must be a real number, got {value!r}") from error

    def _scalar_bar_columns(self, df, columns):
        timestamps = np.empty(len(df), dtype=np.int64)
        values = np.empty((5, len(df)), dtype=np.float64)
        names = ("open", "high", "low", "close", "volume")
        positions = [df.columns.get_loc(columns[name]) if name in columns else None for name in names]
        source = df[columns["timestamp"]].array if "timestamp" in columns else None
        # The old iterrows() converter used this common row dtype. Preserve its
        # OHLCV promotion and scalar errors without constructing a Series per row.
        # Read timestamps separately so integer epochs never promote to float.
        for offset, (idx, row) in enumerate(zip(df.index, df.to_numpy())):
            stamp = idx if isinstance(idx, pd.Timestamp) else source[offset]
            timestamps[offset] = timestamp_value(stamp, offset, self._timestamp_unit)
            for field, position in enumerate(positions):
                values[field, offset] = Backtest._float_value(row[position], names[field], offset) if position is not None else 0.0
        return timestamps, values

    def _to_bars(self, df):
        return _rust._bars_from_columns(self._bar_columns(df), validation=self._data_mode.value)

    def _engine(self, df, columns=None):
        engine = _rust.Engine._from_columns(  # type: ignore
            self._bar_columns(df) if columns is None else columns,
            self._cash,
            self._commission,
            self._spread,
            self._contract_size,
            self._quote_to_account,
            swap=self._swap,
            margin=self._margin,
            validation=self._data_mode.value,
        )
        if self._execution_timing is not None:
            engine._set_execution_timing(*self._execution_timing)
        return engine

    def run(self):
        if self._strategy_class is None:
            raise ValueError("Backtest needs a strategy_class to run(); use optimize() for signal functions")

        self._stats = None
        self._report_df = None
        self._report_timestamps = None
        self._report_snapshot = None
        report_df = self._df.copy(deep=True)
        snapshot = ReferenceSnapshot(report_df) if self._data_mode is InputMode.REFERENCE else None
        columns = self._bar_columns(report_df)
        engine = self._engine(report_df, columns)
        strategy = self._strategy_class()
        stats = engine.run(_Adapter(strategy, snapshot))
        if snapshot is not None or self._execution_timing is not None:
            strategy._data_view._set_length(len(report_df))
        if self._execution_timing is not None and not self._execution_timing[1] and stats.open_positions:
            warnings.warn(
                "Some trades remain open at the end of backtest. "
                "Use finalize_trades=True to queue their closure.",
                UserWarning, stacklevel=2,
            )
        self._stats = stats if snapshot is None else snapshot.result(stats)
        self._report_df = report_df
        self._report_timestamps = columns[0].copy()
        self._report_snapshot = snapshot
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

        snapshot = ReferenceSnapshot(self._df.copy(deep=True)) if self._data_mode is InputMode.REFERENCE else None
        frame = self._df if snapshot is None else snapshot.frame
        engine = self._engine(frame)

        names = list(grid)
        combos = [dict(zip(names, values)) for values in itertools.product(*grid.values())]

        signals = []
        for combo in combos:
            signal = np.asarray(signal_fn(frame, **combo), dtype=float)
            if signal.ndim != 1:
                raise ValueError(f"signal_fn must return a one-dimensional signal for {combo}")
            if len(signal) != len(self._df):
                raise ValueError(
                    f"signal_fn returned {len(signal)} values for {combo}, expected "
                    f"{len(self._df)} (one per bar)"
                )
            if np.isnan(signal).any():
                raise ValueError(
                    f"signal_fn returned NaN for {combo} — indicator warmup should "
                    "produce 0.0 (flat), not NaN"
                )
            if not np.isfinite(signal).all():
                raise ValueError(f"signal_fn must return finite values for {combo}")
            # ponytail: tolist() is the cheap bridge into Rust. It costs one Python
            # float per bar per combo; swap in the `numpy` crate for a zero-copy
            # PyReadonlyArray1 if this ever shows up in a profile.
            signals.append(signal.tolist())

        results = _rust.run_grid(engine, signals)  # type: ignore
        if snapshot is not None:
            results = [snapshot.result(stats) for stats in results]

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
            timestamps=self._report_timestamps,
            strategy_name=self._strategy_class.__name__,
            filename=filename,
            open_browser=open_browser,
            commission=self._commission,
            spread=self._spread,
            contract_size=self._contract_size,
            quote_to_account=self._quote_to_account,
            swap=self._swap,
            snapshot=self._report_snapshot,
        )
