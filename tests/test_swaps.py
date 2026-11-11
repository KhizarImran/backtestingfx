from pathlib import Path
import tempfile
import unittest

import pandas as pd

from backtestingfx import Backtest, Bar, Engine, Strategy, SwapConfig


DAILY = (1, 1, 1, 1, 1, 1, 1)
CONFIG = (10_000.0, 0.0, 0.0, 100_000.0, 1.0)


def market(*times):
    return pd.DataFrame({name: [1.1] * len(times) for name in ("open", "high", "low", "close")},
                        index=pd.to_datetime([pd.Timestamp(time) for time in times], utc=True))


class Hold(Strategy):
    def next(self):
        if self.index == 0:
            self.buy(1.0)


def signature(stats):
    return (stats.final_cash, stats.equity_curve,
            [(t.entry_price, t.exit_price, t.lot_size, t.is_long, t.pnl,
              t.entry_timestamp, t.exit_timestamp) for t in stats.trades])


class SwapTest(unittest.TestCase):
    def assert_reconciles(self, stats):
        self.assertAlmostEqual(stats.final_cash - stats.initial_cash,
                               sum(trade.pnl for trade in stats.trades), places=7)
        self.assertAlmostEqual(stats.total_swap, sum(trade.swap for trade in stats.trades), places=7)
        self.assertAlmostEqual(stats.total_swap, sum(row.amount for row in stats.swap_charges), places=7)
        self.assertEqual(stats.equity_curve[-1], stats.final_cash)

    def test_disabled_and_zero_rates_preserve_existing_results(self):
        data = market("2026-01-05 23:00", "2026-01-06 00:00", "2026-01-07 00:00")
        results = [Backtest(data, Hold, commission=7.0, spread=0.0001,
                            quote_to_account=1.27, swap=swap).run()
                   for swap in (None, SwapConfig())]
        self.assertEqual(signature(results[0]), signature(results[1]))
        for stats in results:
            self.assertEqual(stats.total_swap, 0.0)
            self.assertEqual(stats.swap_charges, [])
            self.assertTrue(all(trade.swap == 0.0 for trade in stats.trades))
        empty = Backtest(market(), Hold, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual(empty.final_cash, 10_000.0)
        self.assertEqual(empty.swap_charges, [])

    def test_partial_closes_allocate_posted_swap_and_reconcile_cash(self):
        seen = []
        snapshots = []

        class ScaleOut(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(1.0)
                elif self.index == 1:
                    snapshots.append(self.positions[0])
                    self.close_partial(self.positions[0].id, 0.4)
                seen.append((self.cash, self.equity, self.positions[0].swap))

        data = market("2026-01-05 23:00", "2026-01-06 00:00", "2026-01-07 00:00")
        stats = Backtest(data, ScaleOut, commission=7.0, quote_to_account=1.27,
                         swap=SwapConfig(long=-10.0, short=2.0, weekday_multipliers=DAILY)).run()
        self.assertAlmostEqual(seen[0][0], 9993.0)
        self.assertAlmostEqual(seen[1][0], 9980.2)
        self.assertAlmostEqual(seen[2][0], 9974.2)
        self.assertEqual([row[2] for row in seen], [0.0, -6.0, -12.0])
        for cash, equity, _ in seen:
            self.assertAlmostEqual(cash, equity)
        self.assertEqual(snapshots[0].swap, -10.0)
        self.assertEqual([trade.swap for trade in stats.trades], [-4.0, -12.0])
        self.assertAlmostEqual(stats.trades[0].pnl, -9.6)
        self.assertAlmostEqual(stats.trades[1].pnl, -20.4)
        self.assertAlmostEqual(stats.final_cash, 9970.0)
        self.assertEqual(stats.total_swap, -16.0)
        self.assertEqual([row.lot_size for row in stats.swap_charges], [1.0, 0.6])
        self.assert_reconciles(stats)

        class ReduceBefore(Hold):
            def next(self):
                super().next()
                if self.index == 1:
                    self.close_partial(self.positions[0].id, 0.4)

        stats = Backtest(market("2026-01-05 23:00", "2026-01-05 23:59", "2026-01-06 00:00"),
                         ReduceBefore, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual([trade.swap for trade in stats.trades], [0.0, -6.0])
        self.assertEqual(stats.swap_charges[0].lot_size, 0.6)
        self.assert_reconciles(stats)

    def test_swap_config_is_immutable_and_calendar_inputs_are_copied(self):
        dates = {"2026-01-06": 0.0}
        swap = SwapConfig(long=-10.0, date_multipliers=dates)
        dates["2026-01-06"] = 3.0
        returned = swap.date_multipliers
        returned["2026-01-06"] = 2.0
        self.assertEqual(swap.date_multipliers["2026-01-06"], 0.0)
        with self.assertRaises(AttributeError):
            swap.long = 5.0

    def test_long_debits_short_credits_use_account_currency_without_conversion(self):
        class Hedge(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(0.5)
                    self.sell(0.25)

        stats = Backtest(market("2026-01-05 23:00", "2026-01-06 00:00"), Hedge,
                         quote_to_account=1.27, swap=SwapConfig(long=-10.0, short=2.0)).run()
        self.assertEqual(stats.total_swap, -4.5)
        self.assertEqual([row.amount for row in stats.swap_charges], [-5.0, 0.5])
        self.assertEqual([row.position_id for row in stats.swap_charges], [0, 1])
        self.assertEqual([row.is_long for row in stats.swap_charges], [True, False])
        self.assert_reconciles(stats)

    def test_exact_boundary_closes_reentries_and_duplicate_bars(self):
        class Reenter(Strategy):
            def next(self):
                if self.index in (1, 2):
                    self.close_all()
                self.buy(1.0)

        data = market("2026-01-05 23:59:59", "2026-01-06 00:00",
                      "2026-01-06 00:00", "2026-01-06 00:00:01")
        stats = Backtest(data, Reenter, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual(stats.total_swap, -10.0)
        self.assertEqual(len(stats.swap_charges), 1)
        self.assertEqual(stats.swap_charges[0].timestamp, int(data.index[1].timestamp()))
        self.assert_reconciles(stats)
        # A first-bar entry on a cutoff does not pay that cutoff.
        stats = Backtest(market("2026-01-06 00:00", "2026-01-07 00:00"), Hold,
                         swap=SwapConfig(long=-10.0)).run()
        self.assertEqual(stats.total_swap, -30.0)

    def test_close_before_cutoff_avoids_charge_and_stop_at_cutoff_pays_it(self):
        class CloseEarly(Hold):
            def next(self):
                super().next()
                if self.index == 1:
                    self.close_all()

        data = market("2026-01-05 23:58", "2026-01-05 23:59", "2026-01-06 00:00")
        stats = Backtest(data, CloseEarly, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual(stats.total_swap, 0.0)

        class Stop(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(1.0, stop_loss=1.09)
                else:
                    if self.has_positions:
                        raise AssertionError("stop must run before callback")

        data = market("2026-01-05 23:00", "2026-01-06 00:00")
        data.iloc[1] = 1.08
        stats = Backtest(data, Stop, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual(stats.total_swap, -10.0)
        self.assertAlmostEqual(stats.trades[0].exit_price, 1.08)
        self.assert_reconciles(stats)

    def test_calendar_gaps_weekends_triple_day_and_holiday_overrides(self):
        data = market("2026-01-08 23:00", "2026-01-12 00:00")
        stats = Backtest(data, Hold, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual([row.multiplier for row in stats.swap_charges], [1.0, 1.0])
        self.assertEqual(stats.total_swap, -20.0)
        data = market("2026-01-06 23:00", "2026-01-08 00:00")
        stats = Backtest(data, Hold, swap=SwapConfig(long=-10.0)).run()
        self.assertEqual([row.multiplier for row in stats.swap_charges], [3.0, 1.0])
        swap = SwapConfig(long=-10.0, date_multipliers={"2026-01-07": 0, "2026-01-08": 4})
        stats = Backtest(data, Hold, swap=swap).run()
        self.assertEqual([row.multiplier for row in stats.swap_charges], [4.0])
        self.assertEqual(stats.total_swap, -40.0)
        self.assert_reconciles(stats)

    def test_dst_rollovers_keep_local_time_and_actual_utc_ledger(self):
        data = market("2026-03-07 21:00", "2026-03-08 22:00")
        swap = SwapConfig(long=-10.0, timezone="America/New_York", rollover_time="17:00",
                          weekday_multipliers=DAILY)
        stats = Backtest(data, Hold, swap=swap).run()
        expected = pd.to_datetime(["2026-03-07 22:00Z", "2026-03-08 21:00Z"])
        self.assertEqual([row.timestamp for row in stats.swap_charges],
                         [int(stamp.timestamp()) for stamp in expected])
        self.assertEqual(stats.total_swap, -20.0)

    def test_python_and_signal_runs_match_with_independent_calendars(self):
        data = market("2026-01-05 23:00", "2026-01-06 00:00", "2026-01-07 00:00")
        swap = SwapConfig(long=-10.0, short=2.0, weekday_multipliers=DAILY)
        backtest = Backtest(data, swap=swap, commission=7.0, spread=0.0001, quote_to_account=1.27)
        for _ in range(2):
            results = backtest.optimize(lambda df, lots: [lots] * len(df), lots=[0.4, -0.2, 0.0])
            for params, result in results:
                lots = params["lots"]

                class Fixed(Strategy):
                    def next(self):
                        if self.index == 0:
                            if lots > 0:
                                self.buy(lots)
                            elif lots < 0:
                                self.sell(-lots)

                actual = Backtest(data, Fixed, swap=swap, commission=7.0, spread=0.0001,
                                  quote_to_account=1.27).run()
                self.assertEqual(signature(result), signature(actual))
                self.assertEqual(result.total_swap, actual.total_swap)
                self.assert_reconciles(result)

    def test_repeated_runs_and_native_hook_keep_config_but_reset_financing(self):
        data = market("2026-01-05 23:00", "2026-01-06 00:00")
        backtest = Backtest(data, Hold, swap=SwapConfig(long=-10.0))
        first = backtest.run()
        self.assertEqual(signature(first), signature(backtest.run()))
        self.assertEqual(len(first.swap_charges), 1)
        data.index = pd.to_datetime(["2026-01-06 23:00", "2026-01-07 00:00"], utc=True)
        self.assertEqual(backtest.run().total_swap, -30.0)
        self.assertEqual(first.total_swap, -10.0)
        bars = [Bar(stamp, price, price, price, price, 0.0)
                for stamp, price in zip([0, 86400, 172800, 259200], [1.0, 1.1, 1.2, 1.3])]
        engine = Engine(bars, *CONFIG, swap=SwapConfig(long=-10.0, weekday_multipliers=DAILY))
        native = engine.run_native_sma(1, 2, 0.4)
        baseline = Engine(bars, *CONFIG).run_native_sma(1, 2, 0.4)
        self.assertAlmostEqual(native.final_cash, baseline.final_cash - 4.0)
        self.assertEqual(native.total_swap, -4.0)
        self.assertEqual(signature(native), signature(engine.run_native_sma(1, 2, 0.4)))

    def test_invalid_config_and_enabled_timestamps_raise_before_init(self):
        for kwargs in ({"long": float("nan")}, {"short": float("inf")},
                       {"timezone": "invalid"}, {"rollover_time": "24:00"},
                       {"weekday_multipliers": [-1] * 7}, {"weekday_multipliers": [1] * 6},
                       {"date_multipliers": {"2026-02-30": 0}},
                       {"date_multipliers": {"2026-01-01": float("inf")}}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((ValueError, TypeError)):
                    SwapConfig(**kwargs)
        with self.assertRaises(TypeError):
            Backtest(market("2026-01-01"), Hold, swap={"long": -10})
        called = []

        class InitProbe(Strategy):
            def init(self):
                called.append(True)

        with self.assertRaisesRegex(ValueError, "nondecreasing"):
            Backtest(market("2026-01-06", "2026-01-05"), InitProbe,
                     swap=SwapConfig(long=-1)).run()
        self.assertEqual(called, [])
        # Swaps do not introduce timestamp restrictions when disabled.
        Backtest(market("2026-01-06", "2026-01-05"), InitProbe).run()
        with self.assertRaisesRegex(ValueError, "date range"):
            Engine([Bar(8210266876799, 1, 1, 1, 1, 0)], *CONFIG,
                   swap=SwapConfig(timezone="Pacific/Kiritimati")).run_native_sma(1, 1, 0.1)

    def test_report_displays_swap_assumptions_allocations_and_dated_ledger(self):
        backtest = Backtest(market("2026-01-05 23:00", "2026-01-06 00:00"), Hold,
                            swap=SwapConfig(long=-10.0, rollover_time="00:00"))
        backtest.run()
        with tempfile.TemporaryDirectory() as directory:
            path = backtest.plot(Path(directory) / "swap.html", open_browser=False)
            report = Path(path).read_text()
        for text in ("Swap cash flow", "Financing ledger", "Local rollover", "00:00 UTC",
                     "Long swap / lot / rollover", "2026-01-06T00:00:00+00:00", "-10.00"):
            self.assertIn(text, report)

    def test_overflow_error_propagates_without_partially_posting_a_rollover(self):
        accounts = []

        class Huge(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(0.01)
                    self.buy(10.0)
                    accounts.append(self._broker)

        with self.assertRaisesRegex(ValueError, "nonfinite"):
            Backtest(market("2026-01-05 23:00", "2026-01-06 00:00"), Huge,
                     swap=SwapConfig(long=1e308)).run()
        self.assertEqual(accounts[0].cash, 10_000.0)
        self.assertEqual(accounts[0].total_swap, 0.0)
        self.assertTrue(all(position.swap == 0.0 for position in accounts[0].positions()))
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            Backtest(market("2026-01-05 23:00", "2026-01-06 00:00"),
                     swap=SwapConfig(long=1e308)).optimize(
                         lambda df, lots: [lots] * len(df), lots=[0.01, 10.0])


if __name__ == "__main__":
    unittest.main()
