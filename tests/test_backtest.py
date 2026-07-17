import unittest

import pandas as pd

from backtestingfx import Backtest, Strategy


class BuyAndHold(Strategy):
    def next(self):
        self.buy(1.0)


class BacktestTest(unittest.TestCase):
    def test_run_returns_stats_from_python_strategy(self):
        data = pd.DataFrame(
            {
                "open": [1.1],
                "high": [1.1],
                "low": [1.1],
                "close": [1.1],
            },
            index=pd.to_datetime(["2026-01-01"], utc=True),
        )

        stats = Backtest(
            data,
            BuyAndHold,
            cash=10_000.0,
            commission=7.0,
            spread=0.0,
        ).run()

        self.assertEqual(stats.initial_cash, 10_000.0)
        self.assertEqual(stats.final_cash, 9_986.0)
        self.assertEqual(stats.num_trades, 1)
        self.assertEqual(stats.avg_pnl, -14.0)


if __name__ == "__main__":
    unittest.main()
