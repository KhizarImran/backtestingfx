from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtestingfx import Backtest, Strategy, SwapConfig


def market(stamps):
    return pd.DataFrame({"timestamp": stamps, **{name: [1.1] * len(stamps)
                         for name in ("open", "high", "low", "close")}})


class Hold(Strategy):
    def next(self):
        if not self.has_positions:
            self.buy(1.0)


class TimestampTest(unittest.TestCase):
    def test_numeric_timestamps_require_explicit_units(self):
        for stamps in ([1767225600, 1767225601], [0.0, 1.0], ["1767225600", "1767225601"]):
            with self.subTest(stamps=stamps):
                with self.assertRaisesRegex(ValueError, "bar 0.*timestamp_unit"):
                    Backtest(market(stamps), Hold).run()

    def test_numeric_units_preserve_exact_integer_seconds(self):
        for unit, factor in (("s", 1), ("ms", 1000), ("us", 1000000), ("ns", 1000000000)):
            for stamps in ([-factor, 0, 1767225600 * factor],
                           [str(-factor), "0", str(1767225600 * factor)]):
                data = market(stamps)
                backtest = Backtest(data, timestamp_unit=unit)
                self.assertEqual([bar.timestamp for bar in backtest._to_bars(data)],
                                 [-1, 0, 1767225600])
        data = market([2**53 + 1, 2**53 + 3])
        self.assertEqual([bar.timestamp for bar in Backtest(data, timestamp_unit="s")._to_bars(data)],
                         [2**53 + 1, 2**53 + 3])

    def test_fractional_datetimes_floor_without_float_rounding(self):
        stamps = ["1969-12-31T23:59:58.999999999Z", "1969-12-31T23:59:59.999999999Z",
                  "1970-01-01T00:00:00.999999999Z", "2026-01-01T00:00:00.999999999Z"]
        data = market(stamps)
        for indexed in (False, True):
            if indexed:
                data.index = pd.DatetimeIndex(stamps)
            self.assertEqual([bar.timestamp for bar in Backtest(data)._to_bars(data)],
                             [-2, -1, 0, 1767225600])

    def test_numeric_fractions_and_int64_limits(self):
        data = market([-1.001, -0.001, 0.999999999, 1.999999999])
        self.assertEqual([bar.timestamp for bar in Backtest(data, timestamp_unit="s")._to_bars(data)],
                         [-2, -1, 0, 1])
        data = market([-(2**63), 2**63 - 1])
        self.assertEqual([bar.timestamp for bar in Backtest(data, timestamp_unit="s")._to_bars(data)],
                         [-(2**63), 2**63 - 1])

    def test_naive_utc_and_offsets_share_the_native_csv_fixture(self):
        data = pd.read_csv(Path(__file__).parent / "data" / "timestamps.csv")
        self.assertEqual([bar.timestamp for bar in Backtest(data)._to_bars(data)],
                         data.expected_seconds.tolist())
        naive = pd.DatetimeIndex(["2026-11-01 05:30", "2026-11-01 06:30"])
        aware = naive.tz_localize("UTC").tz_convert("America/New_York")
        results = []
        for index in (naive, aware):
            data = market(["ignored", "ignored"])
            data.index = index
            results.append([bar.timestamp for bar in Backtest(data)._to_bars(data)])
        self.assertEqual(results[0], results[1])

    def test_invalid_units_and_numeric_values_are_rejected(self):
        for unit in ("seconds", "m", "", 1, []):
            with self.subTest(unit=unit), self.assertRaisesRegex(ValueError, "timestamp_unit"):
                Backtest(market([]), timestamp_unit=unit)
        for value in (True, np.inf, np.nan, pd.NA, 2**63, -(2**63) - 1, "NaT", "invalid"):
            data = market([value])
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "bar 0.*timestamp"):
                Backtest(data, timestamp_unit="s")._to_bars(data)

    def test_numeric_signals_and_swaps_use_the_same_utc_seconds(self):
        data = market([1767308400000, 1767312000000])  # Jan 1 23:00, Jan 2 00:00 UTC
        swap = SwapConfig(long=-2.0)
        backtest = Backtest(data, Hold, timestamp_unit="ms", swap=swap)
        stats = backtest.run()
        signals = backtest.optimize(lambda df, lots: [lots] * len(df), lots=[1.0])[0][1]
        self.assertEqual(stats.total_swap, -2.0)
        self.assertEqual(stats.swap_charges[0].timestamp, 1767312000)
        self.assertEqual(stats.equity_curve, signals.equity_curve)

    def test_report_uses_the_run_timestamp_snapshot(self):
        try:
            import plotly.io as pio
        except ImportError:
            self.skipTest("optional report dependencies unavailable")
        cases = [
            (market(["1969-12-31T23:59:59.999999999Z", "1970-01-01T00:00:00.999999999Z"]), None),
            (market([1767225600000, 1767229200000]), "ms"),
            (market(["ignored", "2026-01-02"]), None),
        ]
        cases[-1][0].index = pd.Index([pd.Timestamp("2026-01-01"), "use-column"], dtype=object)
        for data, unit in cases:
            with self.subTest(unit=unit, index=data.index):
                backtest = Backtest(data, Hold, timestamp_unit=unit)
                stats = backtest.run()
                data["timestamp"] = ["invalid", "invalid"]
                captured = []
                original = pio.to_html

                def capture(chart, **kwargs):
                    if chart.data[0].type == "candlestick":
                        captured.extend(chart.data[0].x)
                    return original(chart, **kwargs)

                with tempfile.TemporaryDirectory() as directory, patch.object(pio, "to_html", capture):
                    backtest.plot(Path(directory) / "report.html", open_browser=False)
                self.assertEqual([int(stamp.timestamp()) for stamp in pd.to_datetime(captured, utc=True)],
                                 [stats.trades[0].entry_timestamp, stats.trades[0].exit_timestamp])


if __name__ == "__main__":
    unittest.main()
