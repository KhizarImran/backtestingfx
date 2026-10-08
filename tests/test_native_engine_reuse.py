import unittest

from backtestingfx import Bar, Engine


class NativeEngineReuseTest(unittest.TestCase):
    def engine(self):
        bars = [Bar(i, price, price, price, price, 0.0)
                for i, price in enumerate([1.1, 1.2, 1.3, 1.4, 1.5])]
        return Engine(bars, 10_000.0, 7.0, 0.0001, 100_000.0, 1.27)

    def test_native_sma_repeated_runs_match(self):
        engine = self.engine()
        first = engine.run_native_sma(1, 2, 0.1)
        second = engine.run_native_sma(1, 2, 0.1)
        self.assertGreater(first.num_trades, 0)
        self.assertEqual(second.final_cash, first.final_cash)
        self.assertEqual(second.equity_curve, first.equity_curve)
        self.assertEqual(second.num_trades, first.num_trades)
        self.assertEqual([t.pnl for t in second.trades], [t.pnl for t in first.trades])

    def test_new_native_parameters_match_a_fresh_engine(self):
        engine = self.engine()
        engine.run_native_sma(1, 2, 0.1)
        actual = engine.run_native_sma(2, 3, 0.2)
        expected = self.engine().run_native_sma(2, 3, 0.2)
        self.assertEqual(actual.final_cash, expected.final_cash)
        self.assertEqual(actual.equity_curve, expected.equity_curve)
        self.assertEqual(actual.num_trades, expected.num_trades)
        self.assertEqual([t.pnl for t in actual.trades], [t.pnl for t in expected.trades])


if __name__ == "__main__":
    unittest.main()
