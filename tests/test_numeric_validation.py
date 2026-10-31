import math
import unittest

import numpy as np
import pandas as pd

from backtestingfx import Backtest, Bar, Broker, Engine, Strategy
from backtestingfx import _backtestingfx as native


def market():
    return pd.DataFrame(
        {column: [1.1, 1.1] for column in ("open", "high", "low", "close")},
        index=pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
    )


def broker():
    return Broker(10_000.0, 7.0, 0.0001, 100_000.0, 1.27)


def engine():
    return Engine([Bar(i, 1.1, 1.1, 1.1, 1.1, 0.0) for i in range(2)],
                  10_000.0, 7.0, 0.0001, 100_000.0, 1.27)


class NumericValidationTest(unittest.TestCase):
    def test_configuration_rejected_at_all_python_constructors(self):
        defaults = [10_000.0, 0.0, 0.0, 100_000.0, 1.0]
        names = ["cash", "commission", "spread", "contract_size", "quote_to_account"]
        for index, name in enumerate(names):
            invalid = [math.nan, math.inf, -math.inf, -1.0]
            if name not in ("commission", "spread"):
                invalid.append(0.0)
            for value in invalid:
                values = defaults.copy()
                values[index] = value
                for label, constructor in (
                    ("Broker", lambda: Broker(*values)),
                    ("Engine", lambda: Engine([], *values)),
                    ("Backtest", lambda: Backtest(market(), **dict(zip(names, values)))),
                ):
                    with self.subTest(name=name, value=value, constructor=label):
                        with self.assertRaisesRegex(ValueError, name):
                            constructor()

    def test_invalid_orders_leave_cash_positions_and_ids_unchanged(self):
        for method in ("buy", "sell"):
            for name in ("lot_size", "price", "stop_loss", "take_profit"):
                for value in (0.0, -1.0, math.nan, math.inf, -math.inf):
                    with self.subTest(method=method, name=name, value=value):
                        account = broker()
                        args = {"price": 1.1, "lot_size": 0.4, "timestamp": 0,
                                "stop_loss": None, "take_profit": None}
                        args[name] = value
                        with self.assertRaisesRegex(ValueError, name):
                            getattr(account, method)(**args)
                        self.assertEqual(account.cash, 10_000.0)
                        self.assertEqual(account.positions(), [])
                        account.buy(1.1, 0.4, 0, None, None)
                        self.assertEqual(account.positions()[0].id, 0)

    def test_invalid_partial_closes_and_stop_updates_preserve_position(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                account = broker()
                account.buy(1.1, 1.0, 0, 1.09, None)
                cash = account.cash
                with self.assertRaisesRegex(ValueError, "lot_size"):
                    account.close_partial(0, value, 1.1, 1)
                with self.assertRaisesRegex(ValueError, "stop_loss"):
                    account.update_sl(0, value)
                self.assertEqual(account.cash, cash)
                self.assertEqual(account.positions()[0].lot_size, 1.0)
                self.assertEqual(account.positions()[0].stop_loss, 1.09)

    def test_partial_close_noops_and_profit_trailing_stops_still_work(self):
        account = broker()
        account.buy(1.1, 1.0, 0, 1.09, None)
        for lots in (0.0, -1.0):
            account.close_partial(0, lots, 1.1, 1)
        account.close_partial(999, 0.4, 1.1, 1)
        self.assertFalse(account.update_sl(999, 1.09))
        self.assertTrue(account.update_sl(0, 1.105))
        self.assertEqual(account.positions()[0].lot_size, 1.0)
        self.assertAlmostEqual(account.cash, 9993.0)
        account.close_partial(0, 2.0, 1.1, 1)
        self.assertEqual(account.positions(), [])
        self.assertAlmostEqual(account.cash, 9973.3)

    def test_invalid_close_and_equity_prices_raise_before_mutation(self):
        for value in (0.0, -1.0, math.nan, math.inf, -math.inf):
            for method in ("close_all", "close_position", "close_partial", "equity"):
                with self.subTest(method=method, value=value):
                    account = broker()
                    account.buy(1.1, 1.0, 0, None, None)
                    args = {"close_all": (value, 1), "close_position": (0, value, 1),
                            "close_partial": (0, 0.4, value, 1), "equity": (value,)}
                    with self.assertRaisesRegex(ValueError, "price"):
                        getattr(account, method)(*args[method])
                    self.assertEqual(len(account.positions()), 1)
                    self.assertAlmostEqual(account.cash, 9993.0)

    def test_strategy_order_errors_propagate_as_value_errors(self):
        class InvalidOrder(Strategy):
            def next(self):
                self.buy(math.nan)

        with self.assertRaisesRegex(ValueError, "lot_size"):
            Backtest(market(), InvalidOrder).run()

    def test_optimize_rejects_nonfinite_and_malformed_signals(self):
        cases = [([0.0, math.inf], "finite"), ([math.nan, 0.0], "NaN"),
                 ([0.0, -math.inf], "finite"), ([[0.0], [0.0]], "one-dimensional"),
                 (0.1, "one-dimensional"), ([0.1], "one per bar")]
        for signal, message in cases:
            with self.subTest(signal=signal):
                with self.assertRaisesRegex(ValueError, message):
                    Backtest(market()).optimize(lambda df, unused: signal, unused=[1])

    def test_direct_grid_rejects_nonfinite_signals_in_every_combination(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "signal 1.*bar 1.*finite"):
                    native.run_grid(engine(), [[0.0, 0.0], [0.0, value]])
        with self.assertRaisesRegex(ValueError, "one per bar"):
            native.run_grid(engine(), [[0.0]])

    def test_signed_and_flat_signals_remain_valid(self):
        results = Backtest(market()).optimize(
            lambda df, lots: np.full(len(df), lots), lots=[-0.4, 0.0, 0.4],
        )
        self.assertEqual(len(results), 3)
        for params, stats in results:
            self.assertAlmostEqual(stats.final_cash, 10_000.0)
            self.assertEqual(stats.num_trades, 0 if params["lots"] == 0 else 1)

    def test_native_sma_rejects_invalid_sizes_and_windows_without_panicking(self):
        for lots in (0.0, -1.0, math.nan, math.inf, -math.inf):
            with self.subTest(lots=lots):
                with self.assertRaisesRegex(ValueError, "lot_size"):
                    engine().run_native_sma(1, 2, lots)
        for fast, slow in ((0, 2), (1, 0), (3, 1)):
            with self.subTest(fast=fast, slow=slow):
                with self.assertRaisesRegex(ValueError, "fast|slow"):
                    engine().run_native_sma(fast, slow, 0.4)


if __name__ == "__main__":
    unittest.main()
