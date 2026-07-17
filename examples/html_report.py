import math

import pandas as pd

from backtestingfx import Backtest, Strategy


class MovingAverageCycle(Strategy):
    fast = 8
    slow = 24

    def next(self):
        if self.index + 1 < self.slow:
            return

        closes = [bar.close for bar in self.data[-self.slow :]]
        fast_average = sum(closes[-self.fast :]) / self.fast
        slow_average = sum(closes) / self.slow
        should_be_long = fast_average > slow_average

        if self.positions and self.positions[0].is_long != should_be_long:
            self.close_all()

        if not self.positions:
            if should_be_long:
                self.buy(0.1)
            else:
                self.sell(0.1)


def sample_data():
    timestamps = pd.date_range("2025-01-01", periods=360, freq="h", tz="UTC")
    closes = [
        1.1000 + 0.0040 * math.sin(i / 13) + 0.0010 * math.sin(i / 3)
        for i in range(len(timestamps))
    ]
    opens = [closes[0], *closes[:-1]]

    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(open_, close) + 0.0004 for open_, close in zip(opens, closes)],
            "low": [min(open_, close) - 0.0004 for open_, close in zip(opens, closes)],
            "close": closes,
            "volume": [800 + int(300 * abs(math.sin(i / 9))) for i in range(len(timestamps))],
        },
        index=timestamps,
    )


data = sample_data()
backtest = Backtest(
    data,
    MovingAverageCycle,
    cash=10_000,
    commission=3.5,
    spread=0.00002,
)

print(backtest.run())
print(f"\nReport written to: {backtest.plot('backtestingfx-report.html')}")
