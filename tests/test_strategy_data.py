import unittest
import warnings

import numpy as np
import pandas as pd
from backtesting import Backtest as ReferenceBacktest, Strategy as ReferenceStrategy

from backtestingfx import Backtest, Strategy


def market(index):
    close = np.array([1.234, 1.2345, 1.2356, 1.23678, 1.23789])
    return pd.DataFrame(
        {"Open": close - 0.001, "High": close + 0.001,
         "Low": close - 0.002, "Close": close,
         "Signal": pd.array([1, 2, None, 4, 5], dtype="Int64")},
        index=index,
    )


class StrategyDataTest(unittest.TestCase):
    def compare(self, frame, warmup):
        def observe(strategy, seen):
            data = strategy.data
            close = data.Close
            self.assertIs(close, data["Close"])
            self.assertEqual(len(data), len(close))
            self.assertIsInstance(close, np.ndarray)
            self.assertEqual(float(close), close[-1])
            self.assertTrue(bool(close))
            self.assertEqual(close.name, "Close")
            self.assertEqual((close + 1).name, "Close")
            with self.assertRaises(AttributeError):
                _ = data.close
            with self.assertRaises(KeyError):
                _ = data["close"]
            seen.append((
                data.index.copy(), data.df.copy(), data.pip,
                [np.asarray(data[name]).copy() for name in ("Open", "High", "Low", "Close", "Volume")],
                close.s, close[-2:].s, close.df, np.mean(close),
            ))

        expected, actual = [], []

        class Reference(ReferenceStrategy):
            def init(self):
                observe(self, expected)
                if warmup:
                    values = np.ones(len(self.data))
                    values[:warmup] = np.nan
                    self.indicator = self.I(lambda: values)

            def next(self):
                observe(self, expected)

        case = self

        class Actual(Strategy):
            def init(self):
                observe(self, actual)
                self.full_close = self.data.Close

            def execution_warmup(self):
                return warmup

            def next(self):
                observe(self, actual)
                case.assertTrue(np.shares_memory(self.data.Close, self.full_close))
                case.assertEqual(self.data[-1].timestamp, self.data.index[-1])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            ReferenceBacktest(frame, Reference).run()
            bt = Backtest(frame, Actual, data_mode="reference", trade_on_close=False)
            bt.run()

        self.assertEqual(len(actual), len(expected))
        for received, reference in zip(actual, expected):
            pd.testing.assert_index_equal(received[0], reference[0])
            pd.testing.assert_frame_equal(received[1], reference[1])
            self.assertEqual(received[2], reference[2])
            for values, expected_values in zip(received[3], reference[3]):
                np.testing.assert_array_equal(values, expected_values)
                self.assertEqual(values.dtype, expected_values.dtype)
            pd.testing.assert_series_equal(received[4], reference[4])
            pd.testing.assert_series_equal(received[5], reference[5])
            pd.testing.assert_frame_equal(received[6], reference[6])
            self.assertEqual(received[7], reference[7])

    def test_visible_arrays_and_pandas_accessors_match_pinned_reference(self):
        self.compare(market(pd.RangeIndex(5, name="period")), warmup=0)

    def test_sorted_duplicate_subsecond_labels_and_warmup(self):
        labels = pd.date_range("2026-01-01", periods=5, freq="123ns", tz="UTC")
        self.compare(market(labels.take([4, 0, 2, 2, 1])), warmup=2)

    def test_repeated_runs_get_independent_column_snapshots(self):
        frame = market(pd.date_range("2026-01-01", periods=5, freq="h"))
        history, expected = [], []

        class Observe(Strategy):
            def init(self):
                history.append(self.data.Close)

        class Reference(ReferenceStrategy):
            def init(self):
                expected.append(self.data.Close)

            def next(self):
                pass

        bt = Backtest(frame, Observe, data_mode="reference")
        ref = ReferenceBacktest(frame, Reference)
        for _ in range(2):
            bt.run()
            ref.run()

        for actual, reference in zip(history, expected):
            np.testing.assert_array_equal(actual, reference)
        self.assertFalse(np.shares_memory(*history))

        updated = frame.assign(Close=frame["Close"] + 1)
        Backtest(updated, Observe, data_mode="reference").run()
        np.testing.assert_allclose(history[0] + 1, history[2])
        np.testing.assert_array_equal(history[0], frame["Close"])
