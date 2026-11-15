"""Input/index fixtures against the pinned reference, not full trading parity."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import numpy as np
import pandas as pd
from backtesting import Backtest as ReferenceBacktest, Strategy as ReferenceStrategy

from backtestingfx import Backtest, Engine, MarginConfig, Strategy, SwapConfig


def market(index=None):
    prices = np.array([1.0, 1.1, 1.2, 1.3, 1.4])
    return pd.DataFrame({name: prices.copy() for name in ("Open", "High", "Low", "Close")},
                        index=pd.RangeIndex(5, name="period") if index is None else index)


class ReferenceNoop(ReferenceStrategy):
    def init(self):
        pass

    def next(self):
        pass


def reference_frame(data):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ReferenceBacktest(data, ReferenceNoop)._data


class ReferenceInputTest(unittest.TestCase):
    def test_range_index_and_timestamp_feature_are_not_unix_dates(self):
        data = market()
        data["timestamp"] = ["not-a-date"] * 5
        data["feature"] = [10, 20, 30, 40, 50]
        original = data.copy(deep=True)
        seen = {}

        class Observe(Strategy):
            def init(self):
                seen["initial"] = self.data.index.copy()
                seen["frame"] = self.data.df.copy()

            def next(self):
                pd.testing.assert_index_equal(self.data.index, data.index[:self.index + 1])
                self.assert_label(self.data[-1].timestamp)

            def assert_label(self, label):
                if label != data.index[self.index]:
                    raise AssertionError("bar time is not its source label")

        with self.assertWarnsRegex(UserWarning, "not datetime"):
            stats = Backtest(data, Observe, data_mode="reference").run()
        pd.testing.assert_index_equal(seen["initial"], data.index)
        pd.testing.assert_frame_equal(seen["frame"], reference_frame(data))
        pd.testing.assert_index_equal(stats.index, data.index)
        pd.testing.assert_index_equal(stats.equity.index, data.index)
        self.assertEqual(stats.equity.tolist(), [10000.0] * 5)
        pd.testing.assert_frame_equal(data, original)

    def test_exact_datetime_index_survives_duplicates_nanoseconds_and_dst(self):
        indexes = [
            pd.date_range("2026-01-01", periods=5, freq="123ns", tz="UTC", name="observed"),
            pd.DatetimeIndex(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03"]),
            pd.date_range("2026-11-01 04:30", periods=5, freq="30min", tz="UTC").tz_convert("America/New_York"),
        ]
        for index in indexes:
            with self.subTest(index=index):
                data = market(index)
                prefixes = []

                class Observe(Strategy):
                    def next(self):
                        prefixes.append(self.data.index.copy())

                stats = Backtest(data, Observe, data_mode="reference").run()
                pd.testing.assert_index_equal(stats.index, reference_frame(data).index)
                for offset, prefix in enumerate(prefixes):
                    pd.testing.assert_index_equal(prefix, index[:offset + 1])

    def test_sorting_moves_every_column_and_preserves_duplicate_rows(self):
        data = market(pd.DatetimeIndex(["2026-01-03", "2026-01-01", "2026-01-02", "2026-01-01", "2026-01-04"]))
        data["feature"] = [30, 10, 20, 11, 40]
        expected = reference_frame(data)
        seen = []

        class Observe(Strategy):
            def init(self):
                seen.append(self.data.df.copy())

        with self.assertWarnsRegex(UserWarning, "Sorting"):
            stats = Backtest(data, Observe, data_mode="reference").run()
        pd.testing.assert_frame_equal(seen[0], expected)
        pd.testing.assert_index_equal(stats.index, expected.index)

    def test_missing_volume_and_reference_numeric_validation(self):
        cases = [market()]
        for field, value in (("Open", 0.0), ("Close", -1.0), ("High", np.inf),
                             ("Low", 10.0), ("Volume", -1.0), ("Volume", np.inf), ("Volume", np.nan)):
            data = market()
            data[field] = value
            cases.append(data)
        for data in cases:
            with self.subTest(columns=list(data)), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                expected = reference_frame(data)
                captured = []

                class Observe(Strategy):
                    def init(self):
                        captured.append(self.data.df.copy())

                result = Backtest(data, Observe, data_mode="reference").run()
                pd.testing.assert_frame_equal(captured[0], expected)
                self.assertEqual(result.num_trades, 0)
        for data in (market().iloc[:0], market().drop(columns="Open"), market().rename(columns=str.lower)):
            with self.subTest(case=data), self.assertRaises(ValueError):
                Backtest(data, Strategy, data_mode="reference")
            with self.assertRaises(ValueError):
                reference_frame(data)
        for field in ("Open", "High", "Low", "Close"):
            data = market()
            data.loc[data.index[2], field] = np.nan
            with self.assertRaisesRegex(ValueError, "OHLC|NaN"):
                Backtest(data, Strategy, data_mode="reference")

    def test_period_and_numeric_index_normalization_uses_reference_source_policy(self):
        # Pinned upstream's removed pandas APIs break these cases on pandas 3.
        # Lock down its intended source algorithm without reproducing that crash.
        for index in (pd.Index([2, 3, 4, 5, 6], name="steps"),
                      pd.Index([1767225600 + i for i in range(5)], name="epochish")):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = Backtest(market(index), Strategy, data_mode="reference").run()
            expected = pd.to_datetime(index) if index[0] > pd.Timestamp("1975").timestamp() else index
            pd.testing.assert_index_equal(result.index, expected)

    def test_nat_index_labels_follow_reference_sorting_policy(self):
        data = market(pd.DatetimeIndex([pd.Timestamp("2026-01-01"), pd.NaT,
                                        pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-04")]))
        with self.assertWarnsRegex(UserWarning, "Sorting"):
            stats = Backtest(data, Strategy, data_mode="reference").run()
        pd.testing.assert_index_equal(stats.index, reference_frame(data).index)

    def test_trade_and_position_times_map_by_row_offset_not_duplicate_label(self):
        for index in (pd.RangeIndex(10, 20, 2, name="period"),
                      pd.DatetimeIndex(["2026-01-01T00:00:00.000000001Z", "2026-01-01T00:00:00.000000002Z",
                                        "2026-01-01T00:00:00.000000003Z", "2026-01-01T00:00:00.000000003Z",
                                        "2026-01-01T00:00:00.000000004Z"])):
            data = market(index)
            positions = []

            class Native(Strategy):
                def next(self):
                    # Align next-open fills manually until #10 supplies that API.
                    if self.index == 2:
                        self._broker.buy(self._bar.open, 0.01, self._bar.timestamp, None, None)
                        positions.extend(self.positions)
                    elif self.index == 3:
                        self._broker.close_all(self._bar.open, self._bar.timestamp)

            class Reference(ReferenceStrategy):
                def init(self):
                    pass

                def next(self):
                    if len(self.data) == 2:
                        self.buy(size=1000)
                    elif len(self.data) == 3:
                        self.position.close()

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                actual = Backtest(data, Native, data_mode="reference").run()
                expected = ReferenceBacktest(data, Reference).run()
            trade = actual.trades[0]
            self.assertEqual((trade.entry_bar, trade.exit_bar), (2, 3))
            self.assertEqual(trade.entry_time, expected._trades.EntryTime.iloc[0])
            self.assertEqual(trade.exit_time, expected._trades.ExitTime.iloc[0])
            self.assertEqual(trade.entry_timestamp, index[2])
            self.assertEqual(trade.exit_timestamp, index[3])
            self.assertEqual(positions[0].entry_time, index[2])
            self.assertAlmostEqual(trade.pnl, expected._trades.PnL.iloc[0])
            np.testing.assert_allclose(actual.equity.to_numpy(), expected._equity_curve.Equity.to_numpy())

    def test_optimization_signals_receive_the_prepared_frame_order(self):
        data = market(pd.DatetimeIndex(["2026-01-05", "2026-01-01", "2026-01-03", "2026-01-02", "2026-01-04"]))
        data["signal"] = [0.0, 0.01, 0.0, 0.01, 0.0]
        expected = reference_frame(data)
        frames = []

        def signals(frame, key):
            frames.append(frame.copy())
            return frame.signal.to_numpy()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            results = Backtest(data, data_mode="reference").optimize(signals, key=[1, 2])
        for frame in frames:
            pd.testing.assert_frame_equal(frame, expected)
        for _, result in results:
            pd.testing.assert_index_equal(result.index, expected.index)
            self.assertEqual(result.trades[0].entry_time, expected.index[0])

    def test_margin_evidence_is_indexed_and_calendar_swaps_are_rejected(self):
        data = market()

        class TooLarge(Strategy):
            def next(self):
                self.buy(1000.0)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stats = Backtest(data, TooLarge, data_mode="reference", margin=MarginConfig(30)).run()
        self.assertEqual(stats.order_rejections[2].timestamp, data.index[2])
        self.assertEqual(stats.order_rejections[2].bar, 2)
        with self.assertRaisesRegex(ValueError, "swap|calendar"):
            Backtest(data, Strategy, data_mode="reference", swap=SwapConfig(long=-1))

    def test_native_reference_buffers_require_dense_keys_and_disallow_calendars(self):
        values = [np.ones(3, dtype=np.float64) for _ in range(5)]
        config = (10000.0, 0.0, 0.0, 100000.0, 1.0)
        with self.assertRaisesRegex(ValueError, "row|offset|ordinal"):
            Engine._from_columns((np.array([0, 2, 3], dtype=np.int64), values), *config, validation="reference")
        with self.assertRaisesRegex(ValueError, "swap|calendar"):
            Engine._from_columns((np.arange(3, dtype=np.int64), values), *config,
                                 validation="reference", swap=SwapConfig(long=-1))
        with self.assertRaisesRegex(ValueError, "validation"):
            Engine._from_columns((np.arange(3, dtype=np.int64), values), *config, validation="typo")

    def test_results_and_reports_keep_their_own_index_snapshot(self):
        try:
            import plotly.io as pio
        except ImportError:
            self.skipTest("optional report dependency missing")
        data = market(pd.DatetimeIndex(["2026-01-01T00:00:00.000000001+01:00"] * 5, name="time"))

        class Hold(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(0.01)

        bt = Backtest(data, Hold, data_mode="reference")
        first = bt.run()
        expected = first.index.copy()
        data.index = pd.RangeIndex(5)
        captured = []
        original = pio.to_html

        def capture(chart, **kwargs):
            captured.append(chart)
            return original(chart, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(pio, "to_html", capture):
            text = Path(bt.plot(Path(directory) / "index.html", open_browser=False)).read_text()
        pd.testing.assert_index_equal(first.index, expected)
        self.assertEqual(list(captured[0].data[0].x), list(range(5)))
        self.assertIn("2026-01-01T00:00:00.000000001+01:00", text)
        self.assertEqual(first.trades[0].entry_time, expected[0])
        second = bt.run()
        pd.testing.assert_index_equal(second.index, expected)
        pd.testing.assert_index_equal(first.index, expected)

    def test_period_report_keeps_canonical_prices_and_escapes_source_labels(self):
        import plotly.io as pio

        data = market(pd.Index(["<a>", "b", "c", "d", "e"], name="step"))
        data["close"] = 900.0  # A feature must not replace canonical Close in the chart.
        captured = []
        original = pio.to_html

        def capture(chart, **kwargs):
            captured.append(chart)
            return original(chart, **kwargs)

        class Hold(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(0.01)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bt = Backtest(data, Hold, data_mode="reference")
            result = bt.run()
        with tempfile.TemporaryDirectory() as directory, patch.object(pio, "to_html", capture):
            report = Path(bt.plot(Path(directory) / "period.html", open_browser=False)).read_text()
        self.assertIn("&lt;a&gt;", report)
        self.assertIn("4 bars", report)
        self.assertEqual(list(captured[0].data[0].close), data.Close.tolist())
        self.assertEqual(result.trades[0].entry_time, "<a>")

    def test_liquidation_and_reports_map_exact_event_rows(self):
        data = market(pd.date_range("2026-01-01", periods=5, freq="1ns", tz="Asia/Kolkata"))
        data.loc[:, ["Open", "High", "Low", "Close"]] = np.array([1.1, 1.0, 1.3, 1.3, 1.3])[:, None]

        class Hold(Strategy):
            def next(self):
                if self.index == 0:
                    self.buy(0.2)

        bt = Backtest(data, Hold, cash=1000, margin=MarginConfig(30), data_mode="reference")
        result = bt.run()
        self.assertEqual(result.liquidation.time, data.index[1])
        self.assertEqual(result.liquidation.bar, 1)
        self.assertEqual(result.trades[0].exit_bar, 1)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(bt.plot(Path(directory) / "liquidation.html", open_browser=False)).read_text()
        self.assertIn(data.index[1].isoformat(), report)
        self.assertIn("Source index", report)

    def test_native_reference_buffer_checks_missing_prices_without_fx_validation(self):
        from backtestingfx import _backtestingfx as native

        values = [np.ones(3, dtype=np.float64) for _ in range(5)]
        keys = np.arange(3, dtype=np.int64)
        values[4][:] = np.nan
        self.assertEqual(len(native._bars_from_columns((keys, values), validation="reference")), 3)
        with self.assertRaisesRegex(ValueError, "volume"):
            native._bars_from_columns((keys, values))
        values[0][1] = np.nan
        with self.assertRaisesRegex(ValueError, "OHLC|NaN"):
            native._bars_from_columns((keys, values), validation="reference")

    def test_invalid_mode_and_strict_fx_extension_still_enforce_their_contracts(self):
        for value in (None, "", "Reference", 1, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "data_mode"):
                Backtest(market(), Strategy, data_mode=value)
        with self.assertRaisesRegex(ValueError, "timestamp|DatetimeIndex"):
            Backtest(market(), Strategy, data_mode="fx").run()


if __name__ == "__main__":
    unittest.main()
