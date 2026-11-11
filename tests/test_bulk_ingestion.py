"""Compare bulk ingestion with the converter shipped before issue #53."""

import unittest

import numpy as np
import pandas as pd

from backtestingfx import Backtest, Bar, Engine, Strategy


CONFIG = (10_000.0, 7.0, 0.0001, 100_000.0, 1.27)
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def legacy_bars(data):
    columns = {name.lower(): name for name in data.columns}
    bars = []
    for index, row in data.iterrows():
        timestamp = (int(index.timestamp()) if isinstance(index, pd.Timestamp)
                     else int(pd.Timestamp(row[columns["timestamp"]]).timestamp()))
        bars.append(Bar(timestamp, float(row[columns["open"]]),
                        float(row[columns["high"]]), float(row[columns["low"]]),
                        float(row[columns["close"]]),
                        float(row[columns["volume"]]) if "volume" in columns else 0.0))
    return bars


def values(bars):
    return [[getattr(bar, field) for field in FIELDS] for bar in bars]


class Capture:
    def init(self, bars):
        self.history = bars
        self.callbacks = []

    def next(self, bar, broker):
        self.callbacks.append(bar)


def market(index=None):
    return pd.DataFrame(
        {"open": [1.1, 1.2, 1.3], "high": [1.2, 1.3, 1.4],
         "low": [1.0, 1.1, 1.2], "close": [1.15, 1.25, 1.35],
         "volume": [10.0, 20.0, 30.0]},
        index=pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
        if index is None else index,
    )


class BulkIngestionTest(unittest.TestCase):
    def assert_matches_legacy(self, data):
        original = data.copy(deep=True)
        original_dtypes = data.dtypes.copy()
        expected = values(legacy_bars(data))
        backtest = Backtest(data)
        capture = Capture()
        stats = backtest._engine(data).run(capture)
        for bars in (capture.history, capture.callbacks, backtest._to_bars(data)):
            self.assertEqual([bar.timestamp for bar in bars], [row[0] for row in expected])
            np.testing.assert_equal([row[1:] for row in values(bars)],
                                    [row[1:] for row in expected])
        for history, callback in zip(capture.history, capture.callbacks):
            self.assertIsInstance(history, Bar)
            self.assertIsNot(history, callback)
        self.assertEqual(stats.num_trades, 0)
        self.assertEqual(stats.equity_curve, [10_000.0] * (len(data) + 1))
        # pandas deep copies can normalize non-native byte order.
        pd.testing.assert_frame_equal(data, original, check_dtype=False)
        pd.testing.assert_series_equal(data.dtypes, original_dtypes)

    def test_column_layouts_and_scalar_conversion_match_legacy(self):
        data = market()
        variants = [data, data.iloc[::2], data.iloc[::-1],
                    data.drop(columns="volume"), data.rename(columns=str.upper),
                    data.astype(np.float32), data.astype(str),
                    data.astype("Float64"), (data * 100).round().astype("Int64")]
        swapped = data.copy()
        for column in swapped:
            swapped[column] = swapped[column].to_numpy().astype(
                np.dtype(np.float64).newbyteorder("S"))
        variants.append(swapped)
        custom = data.copy()
        custom["feature"] = [object(), object(), object()]
        variants.append(custom)
        for index, variant in enumerate(variants):
            with self.subTest(variant=index):
                self.assert_matches_legacy(variant)

    def test_timestamp_units_timezones_and_fractional_rounding_match_legacy(self):
        indexes = [
            pd.DatetimeIndex(["1969-12-31 23:59:59", "1970-01-01", "2026-01-01"]),
            pd.DatetimeIndex(["1969-12-31 23:59:59.999999999",
                              "1970-01-01 00:00:00.999999999",
                              "2026-01-01 00:00:00.999999999"]),
            pd.DatetimeIndex(["1969-12-31 23:59:58.123456789",
                              "1970-01-01 00:00:00.123456789",
                              "2026-01-01 00:00:00.123456789"]),
            pd.date_range("2026-10-25", periods=3, freq="h", tz="Europe/London"),
        ]
        for index in indexes:
            self.assert_matches_legacy(market(index))
        for unit in ("s", "ms", "us", "ns"):
            index = indexes[0]
            if hasattr(index, "as_unit"):
                index = index.as_unit(unit)
            with self.subTest(unit=unit):
                self.assert_matches_legacy(market(index))
        # DatetimeIndex always takes precedence over the timestamp column.
        data = market()
        data["timestamp"] = ["invalid"] * 3
        self.assert_matches_legacy(data)

    def test_far_epoch_seconds_keep_float_rounding_and_overflow(self):
        index = pd.DatetimeIndex(np.array([2**53 + 1, -(2**53 + 1), 0],
                                          dtype=np.int64).astype("datetime64[s]"))
        self.assert_matches_legacy(market(index))
        index = pd.DatetimeIndex(np.array([2**63 - 1, 0, 1],
                                          dtype=np.int64).astype("datetime64[s]"))
        data = market(index)
        for convert in (legacy_bars, Backtest(data)._engine):
            with self.assertRaises(OverflowError):
                convert(data)

    def test_unaligned_and_readonly_columns_are_normalized_for_run_and_optimize(self):
        data = market()
        unaligned = np.ndarray((3,), dtype=np.float64,
                               buffer=np.zeros(25, dtype=np.uint8), offset=1)
        unaligned[:] = data["open"]
        columns = {name: data[name].to_numpy() for name in data.columns}
        columns["open"] = unaligned
        frame = pd.DataFrame(columns, index=data.index, copy=False)
        self.assertFalse(frame["open"].to_numpy().flags.aligned)
        self.assert_matches_legacy(frame)
        for array in columns.values():
            array.flags.writeable = False
        self.assert_matches_legacy(frame)
        actual = Backtest(frame).optimize(lambda df, lots: [lots] * len(df), lots=[0.1])
        expected = Backtest(data).optimize(lambda df, lots: [lots] * len(df), lots=[0.1])
        self.assertEqual(actual[0][1].equity_curve, expected[0][1].equity_curve)

    def test_timestamp_columns_and_mixed_object_indexes_match_legacy(self):
        for stamps in (["2026-01-01", "2026-01-01T01:00:00+01:00", "2026-01-02 03:00"],
                       [0, 1_000_000_000, -1_000_000_000],
                       [1767225600999999400, 1767225600999999600, -999999400],
                       pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")):
            data = market().reset_index(drop=True)
            data["TimeStamp"] = stamps
            self.assert_matches_legacy(data)
        data = market().reset_index(drop=True)
        data["timestamp"] = ["invalid", "2026-01-02", "invalid"]
        data.index = pd.Index([pd.Timestamp("2026-01-01"), "use-column",
                               pd.Timestamp("2026-01-03")], dtype=object)
        self.assert_matches_legacy(data)

    def test_error_order_matches_row_conversion(self):
        data = market().reset_index(drop=True).astype(object)
        data["timestamp"] = ["2026-01-01", "invalid", "2026-01-03"]
        data.loc[0, "open"] = "bad-price"
        for convert in (legacy_bars, Backtest(data)._to_bars, Backtest(data)._engine):
            with self.assertRaisesRegex(ValueError, "bad-price"):
                convert(data)

    def test_empty_data_and_existing_conversion_errors(self):
        self.assert_matches_legacy(pd.DataFrame(columns=["open", "high", "low", "close"]))
        cases = []
        for value in (None, pd.NA, "bad-number"):
            data = market().astype(object)
            data.loc[data.index[1], "close"] = value
            cases.append(data)
        missing_timestamp = market().reset_index(drop=True)
        cases.append(missing_timestamp)
        for stamp in ("invalid", None, pd.NaT):
            data = missing_timestamp.copy()
            data["timestamp"] = ["2026-01-01", stamp, "2026-01-03"]
            cases.append(data)
        data = market()
        data.index = pd.DatetimeIndex([data.index[0], pd.NaT, data.index[-1]])
        cases.append(data)
        for case in cases:
            with self.subTest(data=case):
                try:
                    legacy_bars(case)
                except Exception as error:
                    expected = type(error)
                else:
                    self.fail("legacy fixture must reject input")
                for convert in (Backtest(case)._to_bars, Backtest(case)._engine):
                    with self.assertRaises(expected):
                        convert(case)
        for data, message in ((market().drop(columns="high"), "missing required columns"),
                              (market().assign(Open=1.0), "duplicate columns")):
            with self.assertRaisesRegex(ValueError, message):
                Backtest(data)._engine(data)

    def test_private_buffer_boundary_rejects_invalid_layout_and_config(self):
        timestamps = np.arange(3, dtype=np.int64)
        prices = [np.ones(3, dtype=np.float64) for _ in range(5)]
        for invalid in (np.ones((3, 1)), np.ones(2), np.ones(6)[::2]):
            with self.subTest(shape=invalid.shape, strides=invalid.strides):
                with self.assertRaises(ValueError):
                    Engine._from_columns((timestamps, [invalid, *prices[1:]]), *CONFIG)
        for invalid in (timestamps.reshape(3, 1), np.arange(6, dtype=np.int64)[::2]):
            with self.assertRaises(ValueError):
                Engine._from_columns((invalid, prices), *CONFIG)
        with self.assertRaises(BufferError):
            Engine._from_columns((timestamps.astype(np.float64), prices), *CONFIG)
        with self.assertRaises(BufferError):
            Engine._from_columns((timestamps, [np.ones(3, dtype=np.float32), *prices[1:]]), *CONFIG)
        with self.assertRaisesRegex(ValueError, "cash"):
            Engine._from_columns((timestamps, prices), -1.0, *CONFIG[1:])

    def test_native_data_owns_values_and_history_is_fresh_on_each_run(self):
        timestamps = np.arange(3, dtype=np.int64)
        prices = [np.full(3, 1.1) for _ in range(5)]
        engine = Engine._from_columns((timestamps, prices), *CONFIG)
        timestamps[:] = -100
        for column in prices:
            column[:] = 9.0
        captures = [Capture(), Capture()]
        for capture in captures:
            engine.run(capture)
            self.assertEqual([bar.timestamp for bar in capture.history], [0, 1, 2])
            self.assertEqual([bar.close for bar in capture.callbacks], [1.1] * 3)
        self.assertIsNot(captures[0].history, captures[1].history)
        for old, new in zip(captures[0].history, captures[1].history):
            self.assertIsNot(old, new)

    def test_private_buffer_boundary_rejects_non_native_byte_order(self):
        timestamps = np.arange(3, dtype=np.int64)
        prices = [np.ones(3, dtype=np.float64) for _ in range(5)]
        foreign = np.dtype(np.float64).newbyteorder("S")
        for field in range(5):
            swapped = prices.copy()
            swapped[field] = swapped[field].astype(foreign)
            with self.subTest(field=field):
                with self.assertRaises((ValueError, BufferError)):
                    Engine._from_columns((timestamps, swapped), *CONFIG)
        with self.assertRaises((ValueError, BufferError)):
            Engine._from_columns((timestamps.astype(np.dtype(np.int64).newbyteorder("S")),
                                  prices), *CONFIG)

    def test_mutating_init_history_list_does_not_change_simulation(self):
        class ReplaceHistory(Capture):
            def init(self, bars):
                super().init(bars)
                bars[:] = [Bar(-1, 9.0, 9.0, 9.0, 9.0, 0.0)]

        data = market()
        capture = ReplaceHistory()
        Backtest(data)._engine(data).run(capture)
        self.assertEqual([bar.close for bar in capture.callbacks], list(data["close"]))
        self.assertEqual(len(capture.callbacks), len(data))
        self.assertEqual(capture.history[0].timestamp, -1)

    def test_repeated_runs_observe_input_changes_and_keep_old_snapshots(self):
        histories = []

        class Hold(Strategy):
            def init(self):
                histories.append(self._bars)

            def next(self):
                if self.index == 0:
                    self.buy(0.1)

        data = market()
        backtest = Backtest(data, Hold)
        first = backtest.run()
        first_report = backtest._report_df
        first_values = values(histories[0])
        first_equity = first.equity_curve
        data.iloc[-1, data.columns.get_loc("close")] = 1.5
        data.index = data.index + pd.Timedelta(days=1)
        second = backtest.run()
        self.assertNotEqual(first.final_cash, second.final_cash)
        self.assertNotEqual(histories[0][-1].timestamp, histories[1][-1].timestamp)
        self.assertEqual(values(histories[0]), first_values)
        self.assertEqual(first.equity_curve, first_equity)
        self.assertEqual(first_report["close"].iloc[-1], 1.35)
        self.assertEqual(backtest._report_df["close"].iloc[-1], 1.5)
        data.drop(index=data.index[-1], inplace=True)
        third = backtest.run()
        self.assertEqual(len(third.equity_curve), 3)
        data.drop(columns="open", inplace=True)
        with self.assertRaises(ValueError):
            backtest.run()
        self.assertIsNone(backtest._stats)
        self.assertIsNone(backtest._report_df)


if __name__ == "__main__":
    unittest.main()
