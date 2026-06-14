import backtestingfx as _rust
import pandas as pd


class Strategy:
    def init(self):
        pass

    def next(self):
        pass

    def buy(self, lot_size, stop_loss=None, take_profit=None):
        self._broker.buy(self._bar.close, lot_size, self._bar.timestamp, stop_loss, take_profit)

    def sell(self, lot_size, stop_loss=None, take_profit=None):
        self._broker.sell(self._bar.close, lot_size, self._bar.timestamp, stop_loss, take_profit)

    def close_all(self):
        self._broker.close_all(self._bar.close, self._bar.timestamp)

    def close_position(self, id):
        self._broker.close_position(id, self._bar.close, self._bar.timestamp)


class _Adapter:
    def __init__(self, strategy):
        self._strategy = strategy

    def init(self, bars):
        self._strategy.bars = bars
        self._strategy.init()

    def next(self, bar, broker):
        self._strategy._bar = bar
        self._strategy._broker = broker
        self._strategy.next()


class Backtest:
    def __init__(self, df, strategy_class, cash=10000.0, commission=0.0, spread=0.0):
        self._df = df
        self._strategy_class = strategy_class
        self._cash = cash
        self._commission = commission
        self._spread = spread

    def _to_bars(self):
        bars = []
        for idx, row in self._df.iterrows():
            if isinstance(idx, pd.Timestamp):
                ts = int(idx.timestamp())
            else:
                ts = int(pd.Timestamp(row["timestamp"]).timestamp())

            bars.append(_rust.Bar(
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0))
            ))
        return bars

    def run(self):
        bars = self._to_bars()
        engine = _rust.Engine(bars, self._cash, self._commission, self._spread)
        strategy = self._strategy_class()
        return engine.run(_Adapter(strategy))
