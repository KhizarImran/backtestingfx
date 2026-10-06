import unittest

import pandas as pd

from backtestingfx import Backtest


class ColumnConversionTest(unittest.TestCase):
    def test_case_variants_convert_all_fields_without_mutating_input(self):
        for names in (
            ("open", "high", "low", "close", "volume", "timestamp"),
            ("Open", "High", "Low", "Close", "Volume", "Timestamp"),
            ("oPeN", "HIGH", "low", "cLoSe", "VOLUME", "TimeStamp"),
        ):
            for use_index in (False, True):
                with self.subTest(names=names, use_index=use_index):
                    data = pd.DataFrame(dict(zip(
                        names,
                        ([1.1, 1.2], [1.3, 1.4], [1.0, 1.1],
                         [1.2, 1.3], [10.0, 20.0],
                         ["2026-01-01 00:00", "2026-01-01 01:00"]),
                    )))
                    if use_index:
                        data.index = pd.to_datetime(data.pop(names[-1]), utc=True)
                    original = data.copy(deep=True)
                    bars = Backtest(data)._to_bars(data)
                    self.assertEqual(
                        [(b.timestamp, b.open, b.high, b.low, b.close, b.volume)
                         for b in bars],
                        [(1767225600, 1.1, 1.3, 1.0, 1.2, 10.0),
                         (1767229200, 1.2, 1.4, 1.1, 1.3, 20.0)],
                    )
                    pd.testing.assert_frame_equal(data, original)

    def test_missing_volume_defaults_to_zero(self):
        data = pd.DataFrame(
            {"Open": [1.1], "High": [1.3], "Low": [1.0], "Close": [1.2]},
            index=pd.to_datetime(["2026-01-01"], utc=True),
        )
        self.assertEqual(Backtest(data)._to_bars(data)[0].volume, 0.0)

    def test_missing_required_columns_raise_clear_error(self):
        data = pd.DataFrame({"Open": [1.1], "Close": [1.2]})
        with self.assertRaisesRegex(ValueError, r"missing required columns: \['high', 'low'\]"):
            Backtest(data)._to_bars(data)

    def test_ambiguous_duplicate_columns_are_rejected(self):
        for duplicate in ("open", "volume", "timestamp"):
            for second in (duplicate, duplicate.title()):
                with self.subTest(duplicate=duplicate, second=second):
                    names = ["open", "high", "low", "close"]
                    if duplicate != "open":
                        names.append(duplicate)
                    names.append(second)
                    data = pd.DataFrame([[1.0] * len(names)], columns=names)
                    original = data.copy(deep=True)
                    with self.assertRaisesRegex(ValueError, "duplicate columns after normalization"):
                        Backtest(data)._to_bars(data)
                    pd.testing.assert_frame_equal(data, original)


if __name__ == "__main__":
    unittest.main()
