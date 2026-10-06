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


class TrailAndScaleOut(Strategy):
    """Buy once, trail the stop up each bar, and scale half out on the third bar."""

    def next(self):
        if not self.positions:
            self.buy(1.0, stop_loss=self._bar.close - 0.0100)
            return
        position = self.positions[0]
        self.update_sl(position.id, self._bar.close - 0.0100)
        if self.index == 3:
            self.close_partial(position.id, 0.5)


class SpreadTest(unittest.TestCase):
    def test_midpoint_fills_equity_and_signal_runs_agree(self):
        data = pd.DataFrame(
            {name: [1.1, 1.1] for name in ("open", "high", "low", "close")},
            index=pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        )
        for is_long in (True, False):
            with self.subTest(is_long=is_long):
                equities = []

                class Hold(Strategy):
                    def next(self):
                        if not self.positions:
                            if is_long:
                                self.buy(1.0)
                            else:
                                self.sell(1.0)
                        equities.append(self.equity)

                backtest = Backtest(data, Hold, commission=7.0, spread=0.0001)
                stats = backtest.run()
                self.assertAlmostEqual(stats.final_cash, 9_976.0)
                self.assertAlmostEqual(stats.trades[0].pnl, -24.0)
                self.assertAlmostEqual(
                    stats.trades[0].entry_price, 1.10005 if is_long else 1.09995
                )
                self.assertAlmostEqual(
                    stats.trades[0].exit_price, 1.09995 if is_long else 1.10005
                )
                self.assertEqual(len(equities), 2)
                for equity in equities:
                    self.assertAlmostEqual(equity, 9_983.0)
                self.assertEqual(len(stats.equity_curve), 3)
                for actual, expected in zip(
                    stats.equity_curve, [10_000.0, 9_983.0, 9_976.0]
                ):
                    self.assertAlmostEqual(actual, expected)

                results = backtest.optimize(
                    lambda df, lots: [lots] * len(df),
                    lots=[1.0 if is_long else -1.0],
                )
                signal_stats = results[0][1]
                self.assertAlmostEqual(signal_stats.final_cash, stats.final_cash)
                self.assertEqual(signal_stats.equity_curve, stats.equity_curve)
                with tempfile.TemporaryDirectory() as directory:
                    report = Path(
                        backtest.plot(
                            Path(directory) / "spread.html", open_browser=False
                        )
                    )
                    self.assertIn(
                        "Bid–ask spread (midpoint data)",
                        report.read_text(encoding="utf-8"),
                    )


class PartialAndTrailingStopTest(unittest.TestCase):
    def test_scaling_out_and_trailing_the_stop_through_the_python_api(self):
        backtest = Backtest(rising_market(bars=6), TrailAndScaleOut, cash=10_000.0)
        stats = backtest.run()

        # two trades from one position: the 0.5 scaled out, then the 0.5 liquidated
        self.assertEqual(stats.num_trades, 2)
        self.assertEqual([trade.lot_size for trade in stats.trades], [0.5, 0.5])
        self.assertEqual(stats.trades[0].entry_price, stats.trades[1].entry_price)

    def test_update_sl_reports_a_missing_position(self):
        recorded = []

        class Probe(Strategy):
            def next(self):
                recorded.append(self.update_sl(999, 1.0))

        Backtest(rising_market(bars=2), Probe, cash=10_000.0).run()
        self.assertEqual(recorded, [False, False])


def rising_market(bars=20):
    closes = [1.1000 + 0.0010 * i for i in range(bars)]
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes},
        index=pd.date_range("2026-01-01", periods=bars, freq="h", tz="UTC"),
    )


class OptimizeTest(unittest.TestCase):
    def test_grid_runs_every_combo_and_ranks_by_metric(self):
        def hold_lots(df, lots):
            return [lots] * len(df)

        backtest = Backtest(rising_market(), cash=10_000.0)
        results = backtest.optimize(hold_lots, lots=[0.1, 0.5, 1.0])

        self.assertEqual(len(results), 3)
        # price only rises, so the biggest long wins and ranking is strictly descending
        self.assertEqual([params["lots"] for params, _ in results], [1.0, 0.5, 0.1])
        returns = [stats.total_return_pct for _, stats in results]
        self.assertEqual(returns, sorted(returns, reverse=True))

    def test_grid_is_the_cartesian_product_and_matches_a_single_run(self):
        def hold_lots(df, lots, unused):
            return [lots] * len(df)

        backtest = Backtest(rising_market(), cash=10_000.0)
        results = backtest.optimize(hold_lots, lots=[0.1, 0.2], unused=["a", "b"])
        self.assertEqual(len(results), 4)

        # a parallel grid run must agree with the same signal run on its own
        alone = backtest.optimize(hold_lots, lots=[0.2], unused=["a"])
        matching = [s for p, s in results if p == {"lots": 0.2, "unused": "a"}]
        self.assertEqual(matching[0].final_cash, alone[0][1].final_cash)

    def test_maximize_picks_the_named_field(self):
        def hold_lots(df, lots):
            return [lots] * len(df)

        backtest = Backtest(rising_market(), cash=10_000.0)
        results = backtest.optimize(hold_lots, maximize="max_drawdown_pct", lots=[0.1, 1.0])

        self.assertEqual(
            [stats.max_drawdown_pct for _, stats in results],
            sorted([stats.max_drawdown_pct for _, stats in results], reverse=True),
        )

    def test_nan_signal_is_rejected_rather_than_silently_held(self):
        def leaky_warmup(df, lots):
            return [float("nan")] + [lots] * (len(df) - 1)

        backtest = Backtest(rising_market(), cash=10_000.0)
        with self.assertRaisesRegex(ValueError, "NaN"):
            backtest.optimize(leaky_warmup, lots=[0.1])

    def test_wrong_length_signal_is_rejected(self):
        backtest = Backtest(rising_market(), cash=10_000.0)
        with self.assertRaisesRegex(ValueError, "one per bar"):
            backtest.optimize(lambda df, lots: [lots] * 3, lots=[0.1])

    def test_empty_grid_is_rejected(self):
        backtest = Backtest(rising_market(), cash=10_000.0)
        with self.assertRaises(ValueError):
            backtest.optimize(lambda df: [])


if __name__ == "__main__":
    unittest.main()
