"""Hand-calculated full-book margin stopout and gap-debt examples."""

import pandas as pd

from backtestingfx import Backtest, MarginConfig, Strategy


class Hold(Strategy):
    def next(self):
        if self.index == 0:
            result = self.buy(0.2)
            assert result.accepted


for second_price, expected in ((1.06, 200.0), (1.0, -1000.0)):
    prices = [1.1, second_price, 1.2]
    data = pd.DataFrame({name: prices for name in ("open", "high", "low", "close")},
                        index=pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    stats = Backtest(data, Hold, cash=1000.0,
                     margin=MarginConfig(leverage=30, maintenance_ratio=0.5)).run()
    # 0.2 * 100000 units; the gap PnL is (second_price - 1.1) * 20000.
    assert abs(stats.final_cash - expected) < 1e-8
    assert abs(sum(trade.pnl for trade in stats.trades) - (expected - 1000.0)) < 1e-8
    assert stats.equity_curve[-1] == stats.equity_curve[-2]
    assert stats.liquidation is not None
    print(f"{stats.liquidation.reason}: settled balance {stats.final_cash:.2f}; "
          f"{stats.liquidation.positions_closed} position closed")
