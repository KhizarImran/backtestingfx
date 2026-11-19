import unittest
import warnings

import numpy as np
import pandas as pd
from backtesting import Backtest as ReferenceBacktest, Strategy as ReferenceStrategy

from backtestingfx import Backtest, Strategy


def market():
    return pd.DataFrame(
        {
            "Open": [10, 12, 16, 18, 22],
            "High": [12, 15, 20, 20, 25],
            "Low": [9, 11, 13, 16, 21],
            "Close": [11, 14, 17, 19, 24],
        },
        index=pd.date_range("2026-01-01", periods=5, freq="123ns", tz="UTC"),
    )


class ExecutionTimingTest(unittest.TestCase):
    def compare(self, actions, *, trade_on_close=False, finalize_trades=False, warmup=0,
                data=None):
        data = market() if data is None else data
        expected_callbacks, actual_callbacks = [], []

        class Reference(ReferenceStrategy):
            def init(self):
                if warmup:
                    self.indicator = self.I(lambda: np.r_[
                        np.full(warmup, np.nan), np.ones(len(data) - warmup)])
                if -1 in actions:
                    actions[-1](self, True)

            def next(self):
                offset = len(self.data) - 1
                expected_callbacks.append((offset, len(self.trades), self.equity))
                if offset in actions:
                    actions[offset](self, True)

        class Actual(Strategy):
            def init(self):
                if -1 in actions:
                    actions[-1](self, False)

            def execution_warmup(self):
                return warmup

            def next(self):
                actual_callbacks.append((self.index, self.position_count, self.equity))
                if self.index in actions:
                    actions[self.index](self, False)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            expected = ReferenceBacktest(data, Reference, trade_on_close=trade_on_close,
                                         finalize_trades=finalize_trades).run()
            actual = Backtest(data, Actual, data_mode="reference", contract_size=1,
                              trade_on_close=trade_on_close,
                              finalize_trades=finalize_trades).run()

        np.testing.assert_allclose(actual_callbacks, expected_callbacks)
        np.testing.assert_allclose(actual.equity, expected["_equity_curve"].Equity)
        self.assertEqual(actual.final_equity, expected["Equity Final [$]"])
        self.assertAlmostEqual(actual.total_return_pct, expected["Return [%]"])
        self.assertEqual(
            [(t.entry_bar, t.exit_bar, t.entry_time, t.exit_time, t.entry_price, t.exit_price)
             for t in actual.trades],
            list(expected["_trades"][["EntryBar", "ExitBar", "EntryTime", "ExitTime",
                                      "EntryPrice", "ExitPrice"]].itertuples(index=False, name=None)),
        )
        self.assertEqual(len(actual.open_positions), len(expected["_strategy"].trades))
        return actual

    def test_market_entry_and_parent_close_before_callback(self):
        actions = {
            1: lambda s, ref: s.buy(size=1) if ref else s.buy(1),
            2: lambda s, ref: s.position.close() if ref else s.close_all(),
        }
        for close in (False, True):
            with self.subTest(trade_on_close=close):
                self.compare(actions, trade_on_close=close)

    def test_last_bar_replay_closes_existing_trade_before_last_entry(self):
        for close in (False, True):
            for finalize in (False, True):
                with self.subTest(trade_on_close=close, finalize_trades=finalize):
                    buy = lambda s, ref: s.buy(size=1) if ref else s.buy(1)
                    result = self.compare({1: buy, 4: buy}, trade_on_close=close,
                                          finalize_trades=finalize)
                    self.assertEqual(len(result.open_positions), 1)
                    self.assertEqual(result.num_trades, int(finalize))

    def test_fill_bar_stop_precedes_target_even_on_target_gap(self):
        def enter(s, ref):
            if ref:
                s.buy(size=1, sl=13, tp=15)
            else:
                s.buy(1, stop_loss=13, take_profit=15)

        for close in (False, True):
            with self.subTest(trade_on_close=close):
                result = self.compare({1: enter}, trade_on_close=close)
                self.assertEqual(result.trades[0].exit_price, 13)

    def test_last_bar_entry_reprocesses_its_bracket(self):
        def enter(s, ref):
            if ref:
                s.buy(size=1, sl=18, tp=25)
            else:
                s.buy(1, stop_loss=18, take_profit=25)

        for close in (False, True):
            with self.subTest(trade_on_close=close):
                result = self.compare({4: enter}, trade_on_close=close, finalize_trades=True)
                self.assertEqual(result.trades[0].exit_price, 25)
                self.assertEqual(result.trades[0].exit_bar, 4)

    def test_gap_through_short_stop_uses_current_open(self):
        def enter(s, ref):
            if ref:
                s.sell(size=1, sl=21)
            else:
                s.sell(1, stop_loss=21)

        result = self.compare({1: enter}, trade_on_close=True)
        self.assertEqual(result.trades[0].exit_price, 22)
        self.assertEqual(result.trades[0].exit_bar, 4)

    def test_open_position_metadata_and_warning(self):
        data = market()

        class Hold(Strategy):
            def next(self):
                if self.index == 1:
                    submitted = self.buy(1)
                    assert submitted.pending and submitted.position_id is None
                    assert self.position_count == 0

        with self.assertWarnsRegex(UserWarning, "Some trades remain open"):
            result = Backtest(data, Hold, data_mode="reference", contract_size=1,
                              trade_on_close=False).run()
        self.assertEqual(result.final_cash, 10_000)
        self.assertEqual(result.final_equity, 10_008)
        self.assertEqual(result.num_trades, 0)
        self.assertEqual(result.open_positions[0].entry_bar, 2)
        self.assertEqual(result.open_positions[0].entry_time, data.index[2])

    def test_init_orders_wait_until_after_indicator_warmup(self):
        self.compare({-1: lambda s, ref: s.buy(size=2) if ref else s.buy(2)}, warmup=2)

    def test_one_bar_has_no_callbacks_or_final_replay(self):
        self.compare({}, data=market().iloc[:1], finalize_trades=True)
