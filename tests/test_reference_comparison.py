from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

try:
    import backtesting
except ImportError:
    backtesting = None


@unittest.skipIf(backtesting is None, "optional comparison requires backtesting==0.6.6")
class ReferenceComparisonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
        from compare_bt import Comparison
        cls.comparison_type = Comparison

    def comparison(self, commission=7.0, decisions=None):
        opens = np.array([1.0, 1.1, 1.2, 1.4, 1.3, 1.5, 1.6])
        closes = np.array([1.05, 1.15, 1.25, 1.35, 1.4, 1.55, 1.65])
        data = pd.DataFrame(
            {"open": opens, "high": np.maximum(opens, closes) + 0.01,
             "low": np.minimum(opens, closes) - 0.01, "close": closes},
            index=pd.date_range("2026-01-01", periods=len(opens), freq="h", tz="UTC"),
        )
        if decisions is None:
            decisions = [0, 1, 0, -1, 1, 0, 0]
        return self.comparison_type(data, commission=commission, decisions=decisions)

    def test_next_open_trades_and_equity_match_hand_calculated_fixture(self):
        comparison = self.comparison()
        reference, _ = comparison.run("backtesting.py")
        actual, _ = comparison.run("backtestingfx")
        comparison.verify(reference, actual)
        self.assertEqual(actual.num_trades, 2)
        self.assertAlmostEqual(actual.final_cash, 10_199.72)
        np.testing.assert_allclose(actual.equity_curve[1:],
                                   [10_000, 10_000, 10_049.93, 10_149.93,
                                    10_099.86, 10_149.79, 10_199.72], rtol=0, atol=1e-8)
        np.testing.assert_allclose([trade.entry_price for trade in actual.trades], [1.2, 1.5])
        np.testing.assert_allclose([trade.exit_price for trade in actual.trades], [1.3, 1.6])
        np.testing.assert_allclose([trade.pnl for trade in actual.trades], [99.86, 99.86])

    def test_no_trade_and_zero_commission_cases_match(self):
        for decisions, expected_trades, equity in (([0] * 7, 0, 10_000),
                                                   ([0, 1, 0, -1, 1, 0, 0], 2, 10_200)):
            comparison = self.comparison(commission=0.0, decisions=decisions)
            reference, _ = comparison.run("backtesting.py")
            actual, _ = comparison.run("backtestingfx")
            comparison.verify(reference, actual)
            self.assertEqual(actual.num_trades, expected_trades)
            self.assertAlmostEqual(actual.final_cash, equity)

    def test_checker_rejects_price_mismatch_even_when_trade_count_matches(self):
        comparison = self.comparison()
        reference, _ = comparison.run("backtesting.py")
        actual, _ = comparison.run("backtestingfx")
        reference["_trades"] = reference["_trades"].copy()
        reference["_trades"].loc[0, "EntryPrice"] += 0.01
        with self.assertRaises(AssertionError):
            comparison.verify(reference, actual)


if __name__ == "__main__":
    unittest.main()
