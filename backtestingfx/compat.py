"""Opt-in backtesting.py 0.6.6 API backed by the Rust market execution loop."""

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtestingfx.backtest import Backtest as _FxBacktest
from backtestingfx.backtest import Strategy as _FxStrategy
from backtestingfx.backtest import _Adapter, _StrategyColumn


__all__ = ("Backtest", "Strategy", "Order", "Position", "Trade")


@dataclass(frozen=True)
class Order:
    """A submitted market order. Live inspection and cancellation are tracked in #24."""

    size: int

    @property
    def is_long(self):
        return self.size > 0

    @property
    def is_short(self):
        return self.size < 0


class Trade:
    """A view of a Rust trade open on the current bar."""

    def __init__(self, strategy, record):
        self._strategy = strategy
        self._record = record

    @property
    def size(self):
        return int(self._record.lot_size) * (1 if self.is_long else -1)

    @property
    def is_long(self):
        return self._record.is_long

    @property
    def is_short(self):
        return not self.is_long

    @property
    def entry_price(self):
        return self._record.entry_price

    @property
    def entry_bar(self):
        return self._record.entry_bar

    @property
    def entry_time(self):
        return self._record.entry_time

    @property
    def pl(self):
        return self.size * (self._strategy.data.Close[-1] - self.entry_price)

    def close(self, portion=1.):
        if not 0 < portion <= 1:
            raise ValueError("portion must be between zero (exclusive) and one")
        self._strategy.close_partial(self._record.id, max(1, round(abs(self.size) * portion)))


class Position:
    """Aggregate exposure for the current strategy."""

    def __init__(self, strategy):
        self._strategy = strategy

    def __bool__(self):
        return bool(self._strategy.trades)

    @property
    def size(self):
        return sum(trade.size for trade in self._strategy.trades)

    @property
    def is_long(self):
        return self.size > 0

    @property
    def is_short(self):
        return self.size < 0

    @property
    def pl(self):
        return sum(trade.pl for trade in self._strategy.trades)

    def close(self, portion=1.):
        for trade in self._strategy.trades:
            trade.close(portion)


class Strategy(_FxStrategy):
    """Subclass and implement init() and next(). Run arguments override class attributes."""

    def __init__(self, broker, data, params):
        super().__init__()
        self._broker = broker
        self._data_view = data
        self._params = params
        self._visible_indicators = None
        self._submission_side = None
        for name, value in params.items():
            if name.startswith("_") or not hasattr(self, name):
                raise AttributeError(f"declare strategy parameter {name!r} as a class attribute")
            setattr(self, name, value)

    @property
    def position(self):
        return Position(self)

    @property
    def trades(self):
        return tuple(Trade(self, record) for record in self.positions)

    def I(self, func, *args, name=None, plot=True, overlay=None, color=None,
          scatter=False, **kwargs):
        """Register a numeric indicator computed over the full input in init()."""
        if self._visible_indicators is not None:
            raise RuntimeError("register indicators in Strategy.init()")
        values = np.asarray(func(*args, **kwargs), dtype=float)
        if values.ndim == 2 and values.shape[0] > values.shape[1]:
            values = values.T
        if values.ndim not in (1, 2) or not values.size or values.shape[-1] != len(self.data):
            raise ValueError("indicator must return one value per bar for each series")
        indicator = values.view(_Indicator)
        indicator.name = name if name is not None else getattr(func, "__name__", None)
        indicator._index = self.data.index
        indicator.scatter = scatter
        return indicator

    def buy(self, *, size=.9999, limit=None, stop=None, sl=None, tp=None, tag=None):
        return self._market_order(super().buy, 1, size, limit, stop, sl, tp, tag)

    def sell(self, *, size=.9999, limit=None, stop=None, sl=None, tp=None, tag=None):
        return self._market_order(super().sell, -1, size, limit, stop, sl, tp, tag)

    def _market_order(self, submit, side, size, limit, stop, sl, tp, tag):
        if any(value is not None for value in (limit, stop, sl, tp, tag)):
            raise NotImplementedError("limit, stop, SL, TP, and tags are tracked in #24 and #25")
        if isinstance(size, (bool, np.bool_)) or not math.isfinite(size) or size <= 0:
            raise ValueError("size must be a positive number")
        if size < 1:
            raise NotImplementedError("fractional buying-power sizing is tracked in #62")
        if int(size) != size:
            raise ValueError("unit size must be a whole number")
        if self._submission_side == -side or self.position.size * side < 0:
            raise NotImplementedError("opposing orders and FIFO netting are tracked in #64")
        submit(size)
        self._submission_side = side
        return Order(side * int(size))

    def execution_warmup(self):
        self._visible_indicators = {
            name: value for name, value in vars(self).items() if isinstance(value, _Indicator)
        }
        first_valid = (
            np.argmax(~np.isnan(np.atleast_2d(value)), axis=1).max()
            for value in self._visible_indicators.values() if not value.scatter
        )
        return max(first_valid, default=0)


class _Indicator(_StrategyColumn):
    def __array_finalize__(self, source):
        super().__array_finalize__(source)
        self.scatter = getattr(source, "scatter", False)


class _CompatAdapter(_Adapter):
    def next(self, bar, broker):
        self._advance(bar, broker)
        strategy = self._strategy
        strategy._submission_side = None
        for name, values in strategy._visible_indicators.items():
            setattr(strategy, name, values[..., :len(strategy.data)])
        strategy.next()


class Backtest:
    """Run a strategy class using the documented backtesting.py compatibility subset."""

    def __init__(self, data, strategy, *, cash=10_000, spread=0., commission=0.,
                 margin=1., trade_on_close=False, hedging=False,
                 exclusive_orders=False, finalize_trades=False):
        if not isinstance(strategy, type) or not issubclass(strategy, Strategy):
            raise TypeError("strategy must be a backtestingfx.compat.Strategy subclass")
        for name, value, default, issue in (
            ("spread", spread, 0., 63), ("commission", commission, 0., 63),
            ("margin", margin, 1., 65), ("hedging", hedging, False, 64),
            ("exclusive_orders", exclusive_orders, False, 64),
        ):
            if value != default:
                raise NotImplementedError(f"{name} compatibility is tracked in #{issue}")
        for name, flag in (("trade_on_close", trade_on_close), ("finalize_trades", finalize_trades)):
            if not isinstance(flag, bool):
                raise TypeError(f"{name} must be a bool")
        if trade_on_close:
            raise NotImplementedError("trade_on_close with contingent exits is tracked in #24")
        self._engine = _FxBacktest(
            data, cash=cash, contract_size=1, data_mode="reference",
            trade_on_close=trade_on_close, finalize_trades=finalize_trades,
        )
        self._strategy = strategy

    def run(self, **kwargs):
        strategy = self._strategy(None, None, kwargs)
        stats = self._engine._run_strategy(lambda: strategy, _CompatAdapter)
        for name, values in strategy._visible_indicators.items():
            setattr(strategy, name, values)
        pnl = pd.Series([trade.pnl for trade in stats.trades], dtype=float)
        deviation = pnl.std()
        sqn = math.sqrt(len(pnl)) * pnl.mean() / deviation if deviation > 0 else np.nan
        return pd.Series({
            "Equity Final [$]": stats.final_equity,
            "Return [%]": stats.total_return_pct,
            "# Trades": stats.num_trades,
            "SQN": sqn,
            "_strategy": strategy,
        }, dtype=object)

    def optimize(self, *, maximize="SQN", method="grid", max_tries=None,
                 constraint=None, return_heatmap=False, return_optimization=False,
                 random_state=None, **kwargs):
        if (method != "grid" or max_tries is not None or constraint is not None
                or return_heatmap or return_optimization or random_state is not None):
            raise NotImplementedError("advanced optimization is tracked in #28")
        if maximize not in ("SQN", "Equity Final [$]", "Return [%]", "# Trades"):
            raise ValueError(f"unsupported optimization objective {maximize!r}")
        if not kwargs:
            raise ValueError("optimize requires at least one parameter")
        names = list(kwargs)
        ranges = (value if pd.api.types.is_list_like(value) else (value,)
                  for value in kwargs.values())
        first = best = None
        best_score = -np.inf
        for values in itertools.product(*ranges):
            result = self.run(**dict(zip(names, values)))
            if first is None:
                first = result
            if result["# Trades"] and result[maximize] > best_score:
                best = result
                best_score = result[maximize]
        if first is None:
            raise ValueError("optimization parameter ranges must be nonempty")
        return first if best is None else best
