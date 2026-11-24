import unittest

import pandas as pd

try:
    import backtesting as reference
    from backtesting.lib import crossover
except ImportError:
    reference = None

from backtestingfx import compat


def sma(values, window):
    return pd.Series(values).rolling(window).mean()


class SmaCross:
    fast = 2
    slow = 4
    quantity = 1

    def init(self):
        self.fast_sma = self.I(sma, self.data.Close, self.fast)
        self.slow_sma = self.I(sma, self.data.Close, self.slow)
        self.trace = []
        self.entries = []

    def next(self):
        self.trace.append((len(self.data), len(self.fast_sma), self.position.size))
        for trade in self.trades:
            entry = (trade.size, trade.entry_bar, trade.entry_price)
            if entry not in self.entries:
                self.entries.append(entry)
        if crossover(self.fast_sma, self.slow_sma) and not self.position:
            self.buy(size=self.quantity)
        elif crossover(self.slow_sma, self.fast_sma):
            self.position.close()


class FxSmaCross(SmaCross, compat.Strategy):
    pass


if reference is not None:
    class ReferenceSmaCross(SmaCross, reference.Strategy):
        pass


def market_data():
    prices = [10, 8, 7, 8, 11, 14, 16, 17, 16, 14, 12, 10, 9,
              8, 10, 13, 16, 22, 24, 23, 20, 18, 16, 14, 12]
    close = pd.Series(prices, index=pd.date_range("2026-01-01", periods=len(prices), freq="D"))
    return pd.DataFrame({"Open": close + .25, "High": close + 1,
                         "Low": close - 1, "Close": close})


@unittest.skipIf(reference is None, "comparison requires backtesting==0.6.6")
class CompatibilityTest(unittest.TestCase):
    def assert_matches(self, fx, upstream):
        for name in ("Equity Final [$]", "Return [%]", "# Trades", "SQN"):
            if pd.isna(upstream[name]):
                self.assertTrue(pd.isna(fx[name]))
            else:
                self.assertAlmostEqual(fx[name], upstream[name])
        self.assertEqual(fx["_strategy"].entries, upstream["_strategy"].entries)
        self.assertEqual(fx["_strategy"].trace, upstream["_strategy"].trace)
        self.assertGreater(fx["# Trades"], 0)

    def test_same_strategy_body_runs_on_both_backends(self):
        self.assertEqual(reference.__version__, "0.6.6")
        args = {"slow": 5, "quantity": 3}
        fx = compat.Backtest(market_data(), FxSmaCross).run(**args)
        upstream = reference.Backtest(market_data(), ReferenceSmaCross).run(**args)
        self.assert_matches(fx, upstream)
        self.assertEqual(fx["_strategy"].trace[0][:2], (6, 6))
        self.assertTrue(all(size == 3 for size, *_ in fx["_strategy"].entries))

    def test_short_trade_partial_close_and_finalization(self):
        class PartialClose:
            def next(self):
                if len(self.data) == 2:
                    self.sell(size=3)
                if len(self.data) == 3:
                    self.trades[0].close(portion=.5)

        class FxShort(PartialClose, compat.Strategy):
            pass

        class ReferenceShort(PartialClose, reference.Strategy):
            def init(self):
                pass

        kwargs = {"finalize_trades": True}
        fx = compat.Backtest(market_data(), FxShort, **kwargs).run()
        upstream = reference.Backtest(market_data(), ReferenceShort, **kwargs).run()
        self.assertEqual(fx["# Trades"], 2)
        self.assertEqual(fx["# Trades"], upstream["# Trades"])
        self.assertAlmostEqual(fx["Equity Final [$]"], upstream["Equity Final [$]"])

    def test_same_parameter_grid_runs_on_both_backends(self):
        args = {"quantity": [1, 3], "slow": [4, 5], "maximize": "Equity Final [$]"}
        fx = compat.Backtest(market_data(), FxSmaCross).optimize(**args)
        upstream = reference.Backtest(market_data(), ReferenceSmaCross).optimize(**args)
        self.assert_matches(fx, upstream)
        self.assertEqual(fx["_strategy"].quantity, upstream["_strategy"].quantity)
        self.assertEqual(fx["_strategy"].slow, upstream["_strategy"].slow)


class ScopeTest(unittest.TestCase):
    def test_nondefault_costs_and_fx_options_are_rejected(self):
        for option in ("spread", "commission", "margin", "trade_on_close",
                       "hedging", "exclusive_orders"):
            with self.subTest(option=option), self.assertRaisesRegex(NotImplementedError, option):
                value = 2.0 if option == "margin" else True
                compat.Backtest(market_data(), compat.Strategy, **{option: value})
        with self.assertRaisesRegex(TypeError, "contract_size"):
            compat.Backtest(market_data(), compat.Strategy, contract_size=100_000)

    def test_returned_order_and_open_position_types(self):
        class OneTrade(compat.Strategy):
            def next(self):
                if len(self.data) == 2:
                    self.order = self.buy(size=2)
                if len(self.data) == 3:
                    self.trade = self.trades[0]
                    self.position.close()

        run = compat.Backtest(market_data(), OneTrade).run()
        strategy = run["_strategy"]
        self.assertIsInstance(strategy.order, compat.Order)
        self.assertIsInstance(strategy.position, compat.Position)
        self.assertIsInstance(strategy.trade, compat.Trade)
        self.assertEqual(run["# Trades"], 1)

    def test_fractional_default_and_pending_orders_do_not_use_fx_semantics(self):
        class Fractional(compat.Strategy):
            def next(self):
                self.buy()

        class Pending(compat.Strategy):
            def next(self):
                self.buy(size=1, limit=9)

        with self.assertRaisesRegex(NotImplementedError, "#62"):
            compat.Backtest(market_data(), Fractional).run()
        with self.assertRaisesRegex(NotImplementedError, "#24"):
            compat.Backtest(market_data(), Pending).run()
