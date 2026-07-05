from typing import Any

from backtestingfx import _backtestingfx as _rust  # type: ignore
import pandas as pd


class Strategy:
    def __init__(self):
        self._bars: Any = None
        self._bar: Any = None
        self._broker: Any = None
        self._index: int = 0

    @property
    def positions(self):
        return self._broker.positions() if self._broker else []

    @property
    def data(self):
        return self._bars[: self._index + 1] if self._bars else []

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
        strategy_class,
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

    def _to_bars(self):
        required = {"open", "high", "low", "close"}
        missing = required - set(self._df.columns.str.lower())
        if missing:
            raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

        bars = []
        for idx, row in self._df.iterrows():
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

    def run(self):
        bars = self._to_bars()
        engine = _rust.Engine(  # type: ignore
            bars,
            self._cash,
            self._commission,
            self._spread,
            self._contract_size,
            self._quote_to_account,
        )
        strategy = self._strategy_class()
        return engine.run(_Adapter(strategy))
