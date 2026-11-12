import unittest

import numpy as np
import pandas as pd

from backtestingfx import Backtest, Bar, Engine, Strategy


CONFIG = (10_000.0, 0.0, 0.0, 100_000.0, 1.0)


def market():
    return pd.DataFrame({"open": [1.1, 1.2], "high": [1.3, 1.4],
                         "low": [1.0, 1.1], "close": [1.2, 1.3], "volume": [0.0, 10.0]},
                        index=pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))


class DirectNoop:
    def init(self, bars):
        pass

    def next(self, bar, broker):
        pass


class MarketDataValidationTest(unittest.TestCase):
    def test_invalid_ohlc_and_volume_raise_before_strategy_initialization(self):
        initialized = []

        class Probe(Strategy):
            def init(self):
                initialized.append(True)

        for field in ("open", "high", "low", "close", "volume"):
            invalid = [float("nan"), float("inf"), -float("inf"), -1.0]
            if field != "volume":
                invalid.append(0.0)
            for value in invalid:
                data = market()
                data.loc[data.index[1], field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, f"bar 1.*{field}"):
                        Backtest(data, Probe).run()
        self.assertEqual(initialized, [])

    def test_invalid_ranges_are_rejected_by_all_dataframe_paths(self):
        for field, value in (("low", 1.25), ("high", 1.25), ("open", 1.5), ("close", 0.9)):
            data = market()
            data.loc[data.index[1], field] = value
            for convert in (Backtest(data)._to_bars, Backtest(data)._engine):
                with self.subTest(field=field, convert=convert.__name__):
                    with self.assertRaisesRegex(ValueError, "bar 1.*OHLC"):
                        convert(data)

    def test_duplicate_backwards_and_second_colliding_timestamps_are_rejected(self):
        for index in (pd.DatetimeIndex(["2026-01-01", "2026-01-01"]),
                      pd.DatetimeIndex(["2026-01-02", "2026-01-01"]),
                      pd.DatetimeIndex(["2026-01-01 00:00:00.1", "2026-01-01 00:00:00.2"])):
            data = market()
            data.index = index
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, "bar 1.*timestamp.*strictly increasing"):
                    Backtest(data, Strategy).run()

    def test_direct_bar_and_engine_reject_bad_data(self):
        for field in range(1, 6):
            args = [0, 1.1, 1.1, 1.1, 1.1, 0.0]
            args[field] = float("nan")
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "bar 0"):
                    Bar(*args)
        for timestamps in ((1, 1), (1, 0)):
            bars = [Bar(stamp, 1.1, 1.1, 1.1, 1.1, 0.0) for stamp in timestamps]
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                Engine(bars, *CONFIG)

    def test_private_buffer_factory_validates_values_and_order(self):
        values = [np.ones(2, dtype=np.float64) for _ in range(5)]
        for field in range(5):
            invalid = [column.copy() for column in values]
            invalid[field][1] = float("inf")
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "bar 1"):
                    Engine._from_columns((np.arange(2, dtype=np.int64), invalid), *CONFIG)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            Engine._from_columns((np.array([1, 1], dtype=np.int64), values), *CONFIG)

    def test_missing_invalid_and_unsupported_timestamp_sources_are_actionable(self):
        cases = [market().reset_index(drop=True)]
        for value in (None, pd.NaT, "invalid", float("inf")):
            data = market().reset_index(drop=True)
            data["timestamp"] = ["2026-01-01", value]
            cases.append(data)
        data = market()
        data.index = pd.DatetimeIndex([data.index[0], pd.NaT])
        cases.append(data)
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "timestamp|DatetimeIndex"):
                    Backtest(data, Strategy).run()
        for data in ([], pd.Series([1.0]), np.ones((2, 4))):
            with self.assertRaisesRegex(TypeError, "DataFrame"):
                Backtest(data, Strategy).run()

    def test_optimization_rejects_data_before_calling_signal_function(self):
        calls = []
        data = market()
        data.loc[data.index[1], "close"] = float("nan")

        def signal(df, lots):
            calls.append(lots)
            return np.full(len(df), lots)

        with self.assertRaisesRegex(ValueError, "bar 1.*close"):
            Backtest(data).optimize(signal, lots=[0.1, 0.2])
        self.assertEqual(calls, [])

    def test_empty_and_flat_candles_remain_valid(self):
        data = pd.DataFrame(columns=["open", "high", "low", "close"])
        stats = Backtest(data, Strategy).run()
        self.assertEqual(stats.equity_curve, [10_000.0])
        self.assertEqual(stats.num_trades, 0)
        stats = Engine([], *CONFIG).run(DirectNoop())
        self.assertEqual(stats.final_cash, 10_000.0)
        flat = pd.DataFrame({name: [1.1, 1.1] for name in ("open", "high", "low", "close")},
                            index=pd.date_range("2026-01-01", periods=2, freq="s"))
        self.assertEqual(Backtest(flat, Strategy).run().final_cash, 10_000.0)


if __name__ == "__main__":
    unittest.main()
