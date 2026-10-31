import unittest

import pandas as pd

from backtestingfx import Backtest, Strategy


class IntrabarExecutionTest(unittest.TestCase):
    def test_gap_fills_and_intrabar_precedence_through_python_strategy(self):
        # Offsets describe a long; reflect prices for a short. Include gaps whose
        # ranges never reach the old level, and gaps that reverse through both levels.
        cases = [
            ("stop gap", -0.02, -0.02, -0.02, -0.02, -0.02),
            ("stop gap recovers", -0.02, -0.02, 0.02, 0.0, -0.02),
            ("target gap", 0.02, 0.02, 0.02, 0.02, 0.02),
            ("target gap reverses", 0.02, -0.02, 0.02, 0.0, 0.02),
            ("stop at open", -0.01, -0.02, 0.02, 0.0, -0.01),
            ("target at open", 0.01, -0.02, 0.02, 0.0, 0.01),
            ("intrabar stop", 0.0, -0.015, 0.005, 0.002, -0.01),
            ("intrabar target", 0.0, -0.005, 0.015, 0.002, 0.01),
            ("ambiguous range", 0.0, -0.02, 0.02, 0.0, -0.01),
            ("no trigger", 0.0, -0.005, 0.005, 0.002, 0.002),
        ]
        index = pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")
        for is_long in (True, False):
            direction = 1 if is_long else -1
            for name, opening, low, high, close, expected_exit in cases:
                with self.subTest(is_long=is_long, case=name):
                    seen = []
                    stop = 1.1 - direction * 0.01
                    target = 1.1 + direction * 0.01

                    class EnterOnce(Strategy):
                        def next(self):
                            seen.append(len(self.positions))
                            if self.index == 0:
                                order = self.buy if is_long else self.sell
                                order(0.4, stop_loss=stop, take_profit=target)

                    extremes = [1.1 + direction * low, 1.1 + direction * high]
                    data = pd.DataFrame(
                        {
                            "open": [1.1, 1.1 + direction * opening],
                            "high": [1.1, max(extremes)],
                            "low": [1.1, min(extremes)],
                            "close": [1.1, 1.1 + direction * close],
                        },
                        index=index,
                    )
                    stats = Backtest(
                        data, EnterOnce, commission=7.0, spread=0.0001,
                        quote_to_account=1.27,
                    ).run()
                    self.assertEqual(stats.num_trades, 1)
                    trade = stats.trades[0]
                    self.assertEqual(trade.is_long, is_long)
                    self.assertAlmostEqual(trade.lot_size, 0.4)
                    self.assertAlmostEqual(trade.entry_price, 1.1 + direction * 0.00005)
                    self.assertAlmostEqual(
                        trade.exit_price, 1.1 + direction * (expected_exit - 0.00005)
                    )
                    pnl = (expected_exit - 0.0001) * 100_000 * 0.4 * 1.27 - 5.6
                    self.assertAlmostEqual(trade.pnl, pnl)
                    self.assertAlmostEqual(stats.final_cash, 10_000 + pnl)
                    self.assertAlmostEqual(stats.equity_curve[-1], stats.final_cash)
                    self.assertEqual(trade.entry_timestamp, int(index[0].timestamp()))
                    self.assertEqual(trade.exit_timestamp, int(index[1].timestamp()))
                    self.assertEqual(seen, [0, 1 if name == "no trigger" else 0])

    def test_new_entries_are_checked_from_the_following_bar(self):
        for is_long in (True, False):
            for count in (1, 2):
                with self.subTest(is_long=is_long, count=count):
                    seen = []

                    class EnterOnce(Strategy):
                        def next(self):
                            seen.append(len(self.positions))
                            if self.index == 0:
                                if is_long:
                                    self.buy(1.0, stop_loss=1.09, take_profit=1.11)
                                else:
                                    self.sell(1.0, stop_loss=1.11, take_profit=1.09)

                    index = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
                    data = pd.DataFrame(
                        {"open": [1.1] * count, "high": [1.12] * count,
                         "low": [1.08] * count, "close": [1.1] * count},
                        index=index,
                    )
                    stats = Backtest(data, EnterOnce, commission=0, spread=0).run()
                    self.assertEqual(stats.num_trades, 1)
                    trade = stats.trades[0]
                    self.assertEqual(trade.entry_timestamp, int(index[0].timestamp()))
                    self.assertEqual(trade.exit_timestamp, int(index[-1].timestamp()))
                    if count == 1:
                        # Final liquidation at close: the entry bar's range is not replayed.
                        self.assertAlmostEqual(trade.exit_price, 1.1)
                        self.assertAlmostEqual(trade.pnl, 0.0)
                        self.assertEqual(seen, [0])
                    else:
                        # Both levels touched on the next bar: stop is processed before next().
                        self.assertAlmostEqual(trade.exit_price, 1.09 if is_long else 1.11)
                        self.assertAlmostEqual(trade.pnl, -1000.0)
                        self.assertEqual(seen, [0, 0])


if __name__ == "__main__":
    unittest.main()
