import unittest

import pandas as pd

from backtestingfx import Backtest, Bar, Broker, Engine, Strategy


def bars():
    return [Bar(i, price, price, price, price, 0.0)
            for i, price in enumerate((1.1, 1.2, 1.3))]


def engine():
    return Engine(bars(), 10_000.0, 7.0, 0.0001, 100_000.0, 1.27)


class CallbackContractTest(unittest.TestCase):
    def test_empty_engine_does_not_require_a_next_method(self):
        class InitOnly:
            def init(self, data):
                if data:
                    raise AssertionError("expected no bars")

        stats = Engine([], 10_000.0, 0.0, 0.0, 100_000.0, 1.0).run(InitOnly())
        self.assertEqual(stats.num_trades, 0)
        self.assertEqual(stats.equity_curve, [10_000.0])

    def test_strategy_position_inspection_before_run_and_after_fills(self):
        seen = []
        strategy = Strategy()
        self.assertFalse(strategy.has_positions)
        self.assertEqual(strategy.position_count, 0)

        class Probe(Strategy):
            def next(self):
                seen.append((self.index, self.position_count, self.has_positions))
                if self.index == 0:
                    self.buy(0.4, stop_loss=1.09)
                    self.buy(0.6)
                    self.assert_state(2)
                    self.close_partial(self.positions[0].id, 0.2)
                    self.assert_state(2)
                elif self.index == 1:
                    # The first position stopped out before this callback.
                    self.close_all()
                    self.assert_state(0)

            def assert_state(self, count):
                if self.position_count != count or self.has_positions != (count > 0):
                    raise AssertionError("position inspection differs from live broker state")

        data = pd.DataFrame(
            {column: [1.1, 1.08, 1.1] for column in ("open", "high", "low", "close")},
            index=pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
        )
        stats = Backtest(data, Probe).run()
        self.assertEqual(seen, [(0, 0, False), (1, 1, True), (2, 0, False)])
        self.assertEqual(stats.num_trades, 3)
        self.assertAlmostEqual(stats.final_cash - 10_000.0, sum(t.pnl for t in stats.trades))

    def test_broker_count_and_presence_do_not_change_position_snapshots(self):
        account = Broker(10_000.0, 0.0, 0.0, 100_000.0, 1.0)
        self.assertEqual(account.position_count, 0)
        self.assertFalse(account.has_positions)
        account.buy(1.1, 1.0, 0, None, None)
        saved = account.positions()
        account.close_partial(0, 0.4, 1.1, 1)
        self.assertEqual(account.position_count, 1)
        self.assertTrue(account.has_positions)
        self.assertEqual(saved[0].lot_size, 1.0)
        self.assertAlmostEqual(account.positions()[0].lot_size, 0.6)
        account.close_all(1.1, 2)
        self.assertFalse(account.has_positions)
        self.assertEqual(account.position_count, 0)

    def test_callbacks_keep_distinct_bar_objects_and_retained_values(self):
        class Retain:
            def init(self, data):
                self.data = data
                self.seen = []

            def next(self, bar, broker):
                self.seen.append(bar)

        strategy = Retain()
        stats = engine().run(strategy)
        self.assertEqual([bar.timestamp for bar in strategy.seen], [0, 1, 2])
        self.assertEqual([bar.close for bar in strategy.seen], [1.1, 1.2, 1.3])
        for received, original in zip(strategy.seen, strategy.data):
            self.assertIsNot(received, original)
            self.assertEqual(received.close, original.close)
        self.assertEqual(stats.equity_curve, [10_000.0] * 4)

    def test_direct_engine_resolves_callback_replacements_on_later_bars(self):
        seen = []

        class Replace:
            def init(self, data):
                pass

            def next(self, bar, broker):
                seen.append(("first", bar.timestamp))
                self.next = self.replacement

            def replacement(self, bar, broker):
                seen.append(("replacement", bar.timestamp))

        engine().run(Replace())
        self.assertEqual(seen, [("first", 0), ("replacement", 1), ("replacement", 2)])

    def test_callback_errors_propagate_and_engine_can_be_reused(self):
        failure = RuntimeError("callback failure")
        seen = []

        class Fail:
            def init(self, data):
                pass

            def next(self, bar, broker):
                seen.append(bar.timestamp)
                raise failure

        simulation = engine()
        with self.assertRaises(RuntimeError) as raised:
            simulation.run(Fail())
        self.assertIs(raised.exception, failure)
        self.assertEqual(seen, [0])

        class Noop:
            def init(self, data):
                pass

            def next(self, bar, broker):
                pass

        self.assertEqual(simulation.run(Noop()).equity_curve, [10_000.0] * 4)

    def test_adapter_keeps_index_history_and_dynamic_strategy_methods(self):
        seen = []

        class Probe(Strategy):
            def next(self):
                seen.append((self.index, len(self.data), self.data[-1].close))
                self.next = self.replacement

            def replacement(self):
                seen.append((self.index, len(self.data), self.data[-1].close))
                with self_check.assertRaises(IndexError):
                    self.data[self.index + 1]

        self_check = self
        data = pd.DataFrame(
            {column: [1.1, 1.2, 1.3] for column in ("open", "high", "low", "close")},
            index=pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
        )
        Backtest(data, Probe).run()
        self.assertEqual(seen, [(0, 1, 1.1), (1, 2, 1.2), (2, 3, 1.3)])


if __name__ == "__main__":
    unittest.main()
