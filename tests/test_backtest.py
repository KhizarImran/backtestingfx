from pathlib import Path
import tempfile
import unittest

import pandas as pd

from backtestingfx import Backtest, Strategy
from backtestingfx.backtest import _DataView


class DataViewTest(unittest.TestCase):
    def test_window_hides_future_bars_and_slices_correctly(self):
        view = _DataView(["a", "b", "c", "d"])
        view._len = 3  # only the first 3 bars are "visible" so far

        self.assertEqual(len(view), 3)
        self.assertEqual(view[-1], "c")  # last visible, not "d"
        self.assertEqual(view[0], "a")
        self.assertEqual(view[-2:], ["b", "c"])  # trailing slice, no "d"
        self.assertEqual(list(view), ["a", "b", "c"])
        with self.assertRaises(IndexError):
            view[3]  # "d" is in the future, not addressable yet


class BuyAndHold(Strategy):
    def next(self):
        if not self.positions:
            self.buy(1.0)


class BacktestTest(unittest.TestCase):
    def test_run_returns_stats_from_python_strategy(self):
        data = pd.DataFrame(
            {
                "open": [1.1, 1.1],
                "high": [1.1, 1.1],
                "low": [1.1, 1.1],
                "close": [1.1, 1.1],
            },
            index=pd.to_datetime(["2026-01-01 00:00", "2026-01-01 01:00"], utc=True),
        )

        backtest = Backtest(
            data,
            BuyAndHold,
            cash=10_000.0,
            commission=7.0,
            spread=0.0,
        )
        stats = backtest.run()

        self.assertEqual(stats.initial_cash, 10_000.0)
        self.assertEqual(stats.final_cash, 9_986.0)
        self.assertEqual(stats.num_trades, 1)
        self.assertEqual(stats.avg_pnl, -14.0)
        self.assertEqual(stats.equity_curve, [10_000.0, 9_993.0, 9_986.0])
        self.assertEqual(len(stats.trades), 1)
        self.assertEqual(stats.trades[0].pnl, -14.0)
        self.assertEqual(
            stats.trades[0].exit_timestamp - stats.trades[0].entry_timestamp,
            3_600,
        )

        data.drop(index=data.index[-1], inplace=True)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(
                backtest.plot(Path(directory) / "report.html", open_browser=False)
            )
            contents = report.read_text(encoding="utf-8")

            self.assertTrue(report.is_file())
            self.assertIn("BuyAndHold | backtestingfx report", contents)
            self.assertIn("Market replay", contents)
            self.assertIn("Plotly.newPlot", contents)
            self.assertIn("2026-01-01 00:00", contents)
            self.assertIn("2026-01-01 01:00", contents)


if __name__ == "__main__":
    unittest.main()
