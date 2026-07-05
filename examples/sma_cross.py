import pandas as pd
from backtestingfx import Backtest, Strategy


class SmaCross(Strategy):
    fast = 10
    slow = 50

    def next(self):
        if self.index < self.slow:
            return

        closes = [b.close for b in self.data[-self.slow :]]
        fast_sma = sum(closes[-self.fast :]) / self.fast
        slow_sma = sum(closes) / self.slow

        if not self.positions:
            if fast_sma > slow_sma:
                self.buy(0.1)
        else:
            if fast_sma < slow_sma:
                self.close_all()


df = pd.read_csv("data/EURUSD_1H.csv")

stats = Backtest(
    df,
    SmaCross,
    cash=10_000,
    commission=3.5,
    spread=0.00002,
    contract_size=100_000,
).run()

print(stats)
