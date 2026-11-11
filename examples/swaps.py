"""A flat-price position with partial closure and optional overnight financing."""

import math

import pandas as pd

from backtestingfx import Backtest, Strategy, SwapConfig


class ScaleOut(Strategy):
    def next(self):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.close_partial(self.positions[0].id, 0.4)


data = pd.DataFrame(
    {name: [1.1] * 3 for name in ("open", "high", "low", "close")},
    index=pd.to_datetime(["2026-01-05 23:00", "2026-01-06 00:00", "2026-01-07 00:00"], utc=True),
)
stats = Backtest(data, ScaleOut, commission=7.0, swap=SwapConfig(long=-10.0)).run()
# Tuesday: -10 on 1 lot. Wednesday: -18 on 0.6 lots (triple weight).
# Total financing -28, total round-trip commission -14, no price movement.
assert stats.total_swap == -28.0
assert math.isclose(stats.final_cash, 9958.0, abs_tol=1e-8)
assert math.isclose(sum(trade.pnl for trade in stats.trades), -42.0, abs_tol=1e-8)
print(stats)
for charge in stats.swap_charges:
    print(charge.timestamp, charge.position_id, charge.lot_size, charge.multiplier, charge.amount)
