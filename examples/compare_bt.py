"""Verify matched next-open SMA backtests against backtesting.py.

Install backtesting==0.6.6, then run this file. See benchmark.md for the shared
execution policy, supported comparisons, and timing commands.
"""

import argparse
from pathlib import Path
import time

import numpy as np
import pandas as pd
from backtesting import Backtest as BtBacktest, Strategy as BtStrategy
from backtestingfx import Backtest, Strategy


CONTRACT_SIZE = 100_000.0
CASH = 10_000.0
UNITS = 1000
FAST = 10
SLOW = 50


def load_data(count=None):
    path = Path(__file__).resolve().parents[1] / "data/EURUSD_1H.csv"
    data = pd.read_csv(path)
    data.index = pd.to_datetime(data.pop("timestamp"), utc=True)
    data = data[["open", "high", "low", "close", "volume"]]
    if count is not None:
        if count < 3:
            raise ValueError("comparison needs at least three bars")
        data = pd.concat([data] * (-(-count // len(data))), ignore_index=True).iloc[:count].copy()
        data.index = pd.date_range("2000-01-01", periods=count, freq="h", tz="UTC")
    return data


def sma_decisions(data, fast=FAST, slow=SLOW):
    fast_sma = data["close"].rolling(fast).mean()
    slow_sma = data["close"].rolling(slow).mean()
    return np.where(fast_sma > slow_sma, 1, np.where(fast_sma < slow_sma, -1, 0))


def strategy_types(decisions, units, clocks):
    last_bar = len(decisions) - 1
    lots = units / CONTRACT_SIZE

    class SmaCrossBt(BtStrategy):
        def init(self):
            clocks["backtesting.py"] = time.perf_counter_ns()

        def next(self):
            index = len(self.data) - 1
            if index >= last_bar - 1:
                if index == last_bar - 1 and self.position:
                    self.sell(size=units)
                return
            decision = decisions[index]
            if decision > 0 and not self.position:
                self.buy(size=units)
            elif decision < 0 and self.position:
                self.sell(size=units)

    class SmaCrossFx(Strategy):
        def init(self):
            clocks["backtestingfx"] = time.perf_counter_ns()

        def next(self):
            if self.index == 0:
                return
            price = self._bar.open
            timestamp = self._bar.timestamp
            if self.index == last_bar:
                self._broker.close_all(price, timestamp)
                return
            # Broker calls implement next-open fills explicitly. Strategy.buy()
            # normally fills immediately at the current close in backtestingfx.
            decision = decisions[self.index - 1]
            if decision > 0 and not self.has_positions:
                self._broker.buy(price, lots, timestamp, None, None)
            elif decision < 0 and self.has_positions:
                self._broker.close_all(price, timestamp)

    return SmaCrossBt, SmaCrossFx


class Comparison:
    def __init__(self, data, commission=0.0, units=UNITS, decisions=None):
        if len(data) < 3:
            raise ValueError("comparison needs at least three bars")
        if units < 1 or units != int(units):
            raise ValueError("units must be a positive integer")
        self.data = data
        self.commission = commission
        self.units = int(units)
        self.decisions = sma_decisions(data) if decisions is None else np.asarray(decisions)
        if self.decisions.shape != (len(data),):
            raise ValueError("decisions must contain one value per bar")
        if self.decisions[0] != 0:
            raise ValueError("the first decision must be flat because backtesting.py starts at bar 1")
        self.clocks = {}
        self.bt_strategy, self.fx_strategy = strategy_types(self.decisions, self.units, self.clocks)
        self.bt_data = data.rename(columns=str.capitalize)

    def new_bt(self):
        commission = self.commission
        fee = (lambda size, price: abs(size) * commission / CONTRACT_SIZE) if commission else 0.0
        return BtBacktest(
            self.bt_data, self.bt_strategy, cash=CASH, commission=fee, spread=0.0,
            trade_on_close=False, hedging=False, exclusive_orders=False, finalize_trades=False,
        )

    def new_fx(self):
        return Backtest(self.data, self.fx_strategy, cash=CASH, commission=self.commission,
                        spread=0.0, contract_size=CONTRACT_SIZE, quote_to_account=1.0)

    def run(self, name):
        start = time.perf_counter_ns()
        backtest = self.new_bt() if name == "backtesting.py" else self.new_fx()
        stats = backtest.run()
        end = time.perf_counter_ns()
        initialized = self.clocks[name]
        timings = {"setup_seconds": (initialized - start) / 1e9,
                   "simulation_seconds": (end - initialized) / 1e9,
                   "total_seconds": (end - start) / 1e9}
        return stats, timings

    def verify(self, reference, actual):
        trades = reference["_trades"]
        fx_trades = actual.trades
        np.testing.assert_equal(len(trades), len(fx_trades))
        np.testing.assert_equal(actual.num_trades, len(trades))
        if len(trades):
            np.testing.assert_array_equal(trades["Size"], [self.units] * len(fx_trades))
            np.testing.assert_allclose([trade.lot_size * CONTRACT_SIZE for trade in fx_trades],
                                       trades["Size"], rtol=0, atol=1e-10)
            np.testing.assert_array_equal([trade.is_long for trade in fx_trades], [True] * len(fx_trades))
            for bt_field, fx_field in (("EntryPrice", "entry_price"), ("ExitPrice", "exit_price"),
                                       ("PnL", "pnl")):
                np.testing.assert_allclose(trades[bt_field], [getattr(t, fx_field) for t in fx_trades],
                                           rtol=0, atol=1e-8)
            for bt_field, fx_field in (("EntryTime", "entry_timestamp"), ("ExitTime", "exit_timestamp")):
                expected = [int(stamp.timestamp()) for stamp in trades[bt_field]]
                np.testing.assert_array_equal(expected, [getattr(t, fx_field) for t in fx_trades])
            np.testing.assert_allclose(reference["Win Rate [%]"], actual.win_rate_pct, rtol=0, atol=1e-8)
        # FX prepends an initial-cash observation before the first bar.
        np.testing.assert_allclose(reference["_equity_curve"]["Equity"], actual.equity_curve[1:],
                                   rtol=0, atol=1e-8)
        np.testing.assert_allclose(reference["Equity Final [$]"], actual.final_cash, rtol=0, atol=1e-8)
        np.testing.assert_allclose(reference["Return [%]"], actual.total_return_pct, rtol=0, atol=1e-8)
        np.testing.assert_allclose(-reference["Max. Drawdown [%]"], actual.max_drawdown_pct,
                                   rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual.final_cash - CASH, sum(t.pnl for t in fx_trades),
                                   rtol=0, atol=1e-8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int)
    parser.add_argument("--commission", type=float, default=0.0)
    args = parser.parse_args()
    comparison = Comparison(load_data(args.bars), commission=args.commission)
    bt_result, _ = comparison.run("backtesting.py")
    fx_result, _ = comparison.run("backtestingfx")
    comparison.verify(bt_result, fx_result)
    print(f"PASS: {len(comparison.data):,} bars, {fx_result.num_trades} matching trades, "
          f"final equity {fx_result.final_cash:.8f}, commission {args.commission}/lot/side")
    print("Verified fills, sizes, timestamps, net PnL, full equity curve, return, win rate, and drawdown.")


if __name__ == "__main__":
    main()
