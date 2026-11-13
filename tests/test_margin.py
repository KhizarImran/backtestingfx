from pathlib import Path
import math
import tempfile
import unittest

import numpy as np
import pandas as pd

import backtestingfx as fx


def market(prices):
    return pd.DataFrame({name: prices for name in ("open", "high", "low", "close")},
                        index=pd.date_range("2026-01-01 23:00", periods=len(prices), freq="h", tz="UTC"))


def margin(leverage=30.0, maintenance_ratio=0.5):
    return fx.MarginConfig(leverage=leverage, maintenance_ratio=maintenance_ratio)


class Hold(fx.Strategy):
    def next(self):
        if self.index == 0:
            self.buy(0.2)


class MarginTest(unittest.TestCase):
    def test_admission_includes_costs_and_rejection_is_atomic(self):
        broker = fx.Broker(4000.0, 7.0, 0.0001, 100000.0, 1.0, margin=margin())
        result = broker.buy(1.2, 1.0, 0, None, None)
        self.assertFalse(result.accepted)
        self.assertIsNone(result.position_id)
        self.assertEqual(result.reason, "insufficient_margin")
        self.assertEqual(broker.cash, 4000.0)
        self.assertEqual(broker.position_count, 0)
        self.assertEqual(len(broker.order_rejections), 1)
        self.assertAlmostEqual(broker.order_rejections[0].equity_after_entry, 3983.0)
        smaller = broker.sell(1.2, 0.5, 1, None, None)
        self.assertTrue(smaller.accepted)
        self.assertEqual(smaller.position_id, 0)
        self.assertIsNone(smaller.reason)
        self.assertAlmostEqual(broker.used_margin(1.2), 2000.0)

    def test_exact_boundary_gross_hedges_scaling_and_partial_margin_release(self):
        broker = fx.Broker(10000.0, 0.0, 0.0, 100000.0, 1.0, margin=margin())
        first = broker.buy(1.2, 1.0, 0, None, None)
        self.assertTrue(broker.sell(1.2, 1.0, 0, None, None).accepted)
        self.assertAlmostEqual(broker.used_margin(1.2), 8000.0)
        self.assertAlmostEqual(broker.free_margin(1.2), 2000.0)
        self.assertFalse(broker.buy(1.2, 1.0, 0, None, None).accepted)
        broker.close_partial(first.position_id, 0.5, 1.2, 1)
        self.assertAlmostEqual(broker.used_margin(1.2), 6000.0)
        self.assertTrue(broker.buy(1.2, 1.0, 1, None, None).accepted)
        self.assertAlmostEqual(broker.free_margin(1.2), 0.0)
        converted = fx.Broker(4000.0, 0.0, 0.0, 100000.0, 1.5, margin=margin())
        converted.buy(1.2, 0.5, 0, None, None)
        self.assertAlmostEqual(converted.used_margin(1.2), 3000.0)

    def test_gap_liquidation_halts_callbacks_preserves_debt_and_reconciles(self):
        calls = []

        class Probe(Hold):
            def next(self):
                calls.append(self.index)
                super().next()

        data = market([1.1, 1.0, 1.3])
        stats = fx.Backtest(data, Probe, cash=1000.0, margin=margin()).run()
        self.assertEqual(calls, [0])
        self.assertEqual(stats.account_status, "liquidated")
        self.assertEqual(stats.liquidation.reason, "nonpositive_equity")
        self.assertEqual(stats.liquidation.timestamp, int(data.index[1].timestamp()))
        self.assertEqual(stats.liquidation.positions_closed, 1)
        self.assertAlmostEqual(stats.final_cash, -1000.0)
        self.assertEqual(stats.trades[0].exit_price, 1.0)
        self.assertEqual(len(stats.equity_curve), len(data) + 1)
        self.assertEqual(stats.equity_curve[-1], stats.equity_curve[-2])
        self.assertAlmostEqual(stats.final_cash - stats.initial_cash, sum(t.pnl for t in stats.trades))
        self.assertEqual(stats.sharpe_ratio, 0.0)

    def test_closing_observation_liquidates_after_stops_before_callback(self):
        data = market([1.1, 1.06, 1.2])
        data.loc[data.index[1], ["open", "high"]] = [1.1, 1.1]
        plain = fx.Backtest(data, Hold, cash=1000.0, margin=margin()).run()
        self.assertEqual(plain.liquidation.reason, "maintenance_margin")
        self.assertAlmostEqual(plain.final_cash, 200.0)

        class Protected(fx.Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(0.2, stop_loss=1.09)

        stopped = fx.Backtest(data, Protected, cash=1000.0, margin=margin()).run()
        self.assertIsNone(stopped.liquidation)
        self.assertAlmostEqual(stopped.final_cash, 800.0)
        self.assertEqual(stopped.trades[0].exit_price, 1.09)

    def test_swap_stopout_and_python_signal_equivalence(self):
        data = market([1.1, 1.1, 1.1])
        config = dict(cash=1000.0, margin=margin(), swap=fx.SwapConfig(long=-4000.0))
        bt = fx.Backtest(data, Hold, **config)
        python = bt.run()
        signals = bt.optimize(lambda df, lots: [lots] * len(df), lots=[0.2])[0][1]
        self.assertAlmostEqual(python.final_cash, 200.0)
        self.assertAlmostEqual(python.total_swap, -800.0)
        self.assertEqual(python.liquidation.reason, "maintenance_margin")
        self.assertEqual(python.liquidation.timestamp, python.swap_charges[0].timestamp)
        self.assertEqual(python.equity_curve, signals.equity_curve)
        self.assertEqual(python.trades[0].pnl, signals.trades[0].pnl)
        self.assertEqual(python.liquidation.reason, signals.liquidation.reason)

    def test_callback_and_final_exit_commission_can_exhaust_equity(self):
        class CloseAndRetry(fx.Strategy):
            results = []

            def next(self):
                self.buy(0.1)
                self.close_all()
                self.results.append(self.buy(0.1))

        data = market([1.0, 1.0, 1.0])
        stats = fx.Backtest(data, CloseAndRetry, cash=1000.0, commission=5000.0,
                           margin=margin(leverage=100)).run()
        self.assertEqual(stats.final_cash, 0.0)
        self.assertEqual(stats.equity_curve, [1000.0, 0.0, 0.0, 0.0])
        self.assertEqual(stats.sharpe_ratio, 0.0)
        self.assertEqual(CloseAndRetry.results[0].reason, "account_liquidated")
        self.assertEqual(len(CloseAndRetry.results), 1)
        self.assertEqual(stats.order_rejections[0].reason, "account_liquidated")
        final = fx.Backtest(market([1.0]), Hold, cash=1000.0, commission=2500.0,
                            margin=margin(leverage=100)).run()
        self.assertEqual(final.final_cash, 0.0)
        self.assertEqual(final.liquidation.reason, "nonpositive_equity")
        self.assertEqual(final.liquidation.positions_closed, 0)

    def test_ignored_rejections_persist_in_results_and_runs_reset(self):
        class TooLarge(fx.Strategy):
            def next(self):
                self.buy(10.0)

        bt = fx.Backtest(market([1.1, 1.1]), TooLarge, cash=1000.0, margin=margin())
        for stats in (bt.run(), bt.run()):
            self.assertEqual(stats.final_cash, 1000.0)
            self.assertEqual(stats.num_trades, 0)
            self.assertEqual(len(stats.order_rejections), 2)
            self.assertTrue(all(r.reason == "insufficient_margin" for r in stats.order_rejections))
            self.assertEqual(stats.account_status, "active")
        direct = fx.Engine([fx.Bar(0, 1.1, 1.1, 1.1, 1.1, 0.0)],
                           1000.0, 0.0, 0.0, 100000.0, 1.0, margin=margin())
        self.assertEqual(direct._run_native_noop().margin.leverage, 30.0)
        resized = fx.Backtest(market([1.1, 1.1]), cash=1000.0, commission=7.0,
                             margin=margin()).optimize(lambda df, key: [0.2, 1.0], key=[1])[0][1]
        self.assertEqual(resized.num_trades, 1)
        self.assertEqual(len(resized.order_rejections), 1)
        self.assertAlmostEqual(resized.final_cash, 997.2)
        self.assertAlmostEqual(resized.trades[0].pnl, -2.8)

    def test_margin_configuration_and_derived_overflow_are_checked(self):
        for leverage in (0.0, 0.5, -1.0, math.nan, math.inf):
            with self.subTest(leverage=leverage), self.assertRaisesRegex(ValueError, "leverage"):
                margin(leverage=leverage)
        for ratio in (0.0, 1.0, -0.5, math.nan, math.inf):
            with self.subTest(ratio=ratio), self.assertRaisesRegex(ValueError, "maintenance_ratio"):
                margin(maintenance_ratio=ratio)
        config = margin()
        with self.assertRaises((AttributeError, TypeError)):
            config.leverage = 100
        with self.assertRaisesRegex(TypeError, "margin"):
            fx.Backtest(market([1.1]), Hold, margin={"leverage": 30})
        broker = fx.Broker(1000.0, 0.0, 0.0, 100000.0, 1.0, margin=config)
        self.assertEqual(broker.buy(1.1, np.finfo(float).max, 0, None, None).reason,
                         "arithmetic_overflow")
        self.assertEqual(broker.cash, 1000.0)
        self.assertEqual(broker.position_count, 0)
        with self.assertRaisesRegex(ValueError, "finite|overflow"):
            fx.Backtest(market([1.1, np.finfo(float).max]), Hold, cash=1000.0, margin=config).run()

    def test_disabled_margin_keeps_existing_behavior(self):
        data = market([1.1, 1.2])
        default = fx.Backtest(data, Hold).run()
        disabled = fx.Backtest(data, Hold, margin=None).run()
        self.assertEqual(default.equity_curve, disabled.equity_curve)
        self.assertIsNone(disabled.margin)
        self.assertIsNone(disabled.liquidation)
        self.assertEqual(disabled.order_rejections, [])

    def test_nonfinite_fills_and_aggregate_lots_are_rejected_before_entry(self):
        for is_long in (True, False):
            broker = fx.Broker(1000.0, 0.0, 1.7e308, 1.0, 1.0, margin=margin())
            result = (broker.buy if is_long else broker.sell)(1.7e308, 1e-308, 0, None, None)
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "arithmetic_overflow")
            self.assertEqual(broker.cash, 1000.0)
            self.assertEqual(broker.position_count, 0)
        broker = fx.Broker(1000.0, 0.0, 0.0, 1e-308, 1.0, margin=margin())
        self.assertTrue(broker.buy(1.0, 1e308, 0, None, None).accepted)
        self.assertFalse(broker.buy(1.0, 1e308, 1, None, None).accepted)
        self.assertEqual(broker.position_count, 1)
        self.assertTrue(math.isfinite(broker.used_margin(1.0)))

    def test_manual_exit_overflow_is_atomic_and_marks_the_account_failed(self):
        for mode in ("all", "position", "partial"):
            broker = fx.Broker(1e308, 0.0, 0.0, 1e308, 1.0, margin=margin(leverage=1e308))
            broker.buy(1.0, 1.0, 0, None, None)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "finite|overflow"):
                if mode == "all":
                    broker.close_all(1e308, 1)
                elif mode == "position":
                    broker.close_position(0, 1e308, 1)
                else:
                    broker.close_partial(0, 0.5, 1e308, 1)
            self.assertEqual(broker.cash, 1e308)
            self.assertEqual(broker.position_count, 1)
            self.assertEqual(broker.positions()[0].lot_size, 1.0)
            self.assertEqual(broker.account_status, "failed")

    def test_report_retains_margin_assumptions_and_stopout_evidence(self):
        try:
            import plotly
        except ImportError:
            self.skipTest("optional report dependencies unavailable")
        bt = fx.Backtest(market([1.1, 1.0, 1.3]), Hold, cash=1000.0, margin=margin())
        bt.run()
        with tempfile.TemporaryDirectory() as directory:
            report = Path(bt.plot(Path(directory) / "margin.html", open_browser=False)).read_text()
        self.assertIn("LIQUIDATED", report)
        self.assertIn("nonpositive_equity", report)
        self.assertIn("Margin leverage", report)
        self.assertIn("Margin execution", report)
        self.assertIn("-1,000.00", report)


if __name__ == "__main__":
    unittest.main()
