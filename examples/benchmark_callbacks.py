"""Isolate callback, adapter, indicator, and position-inspection costs.

Run against an installed release build, outside the repository. See
benchmark_callbacks.md for commands, timing boundaries, and interpretation.
"""

import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from backtestingfx import Backtest, Engine, Strategy
from backtestingfx.backtest import _Adapter


CONFIG = (10_000.0, 7.0, 0.0001, 100_000.0, 1.27)
LOT = 0.001


class Noop(Strategy):
    pass


class DirectNoop:
    def init(self, bars):
        self.setup_finished = time.perf_counter_ns()

    def next(self, bar, broker):
        pass


class TimedAdapter(_Adapter):
    def init(self, bars):
        super().init(bars)
        self.setup_finished = time.perf_counter_ns()


class RollingIndicator(Strategy):
    def next(self):
        self.value = sum(bar.close for bar in self.data[-20:]) / min(self.index + 1, 20)


class PrecomputedIndicator(Strategy):
    def init(self):
        closes = np.array([bar.close for bar in self._bars])
        sums = np.concatenate(([0.0], np.cumsum(closes)))
        ends = np.arange(1, len(closes) + 1)
        starts = np.maximum(0, ends - 20)
        self.values = (sums[ends] - sums[starts]) / (ends - starts)

    def next(self):
        self.value = self.values[self.index]


class Hold(Strategy):
    count = 1

    def next(self):
        if self.index == 0:
            for _ in range(self.count):
                self.buy(LOT)


class ReadPositions(Hold):
    def next(self):
        super().next()
        self.observed = len(self.positions)


class ReadCount(Hold):
    def next(self):
        super().next()
        self.observed = self.position_count


class ReadPresence(Hold):
    def next(self):
        super().next()
        self.observed = self.has_positions


class TradeEveryBar(Strategy):
    def next(self):
        self.buy(LOT)
        self.close_all()


def cases():
    result = {
        "native-noop": (None, 0),
        "direct-noop": (DirectNoop, 0),
        "adapter-noop": (Noop, 0),
        "rolling-indicator": (RollingIndicator, 0),
        "precomputed-indicator": (PrecomputedIndicator, 0),
        "trade-every-bar": (TradeEveryBar, None),
    }
    for count in (1, 32):
        for name, base in (("hold", Hold), ("positions", ReadPositions),
                           ("count", ReadCount), ("presence", ReadPresence)):
            if base is ReadCount and not hasattr(Strategy, "position_count"):
                continue
            if base is ReadPresence and not hasattr(Strategy, "has_positions"):
                continue
            result[f"{name}-{count}"] = (type(f"{name}{count}", (base,), {"count": count}), count)
    return result


def assert_result(stats, bars, trades):
    # Flat midpoint: each trade pays one full spread, two commissions, and conversion.
    pnl = -(0.0001 * 100_000 * 1.27 + 2 * 7.0) * LOT
    assert stats.num_trades == trades, (stats.num_trades, trades)
    assert math.isclose(stats.final_cash, 10_000 + trades * pnl, abs_tol=1e-5)
    assert len(stats.equity_curve) == bars + 1
    assert stats.equity_curve[0] == 10_000.0
    assert stats.equity_curve[-1] == stats.final_cash
    if trades == 0:
        assert all(value == 10_000.0 for value in stats.equity_curve)
    assert all(math.isclose(trade.entry_price, 1.10005, abs_tol=1e-12)
               and math.isclose(trade.exit_price, 1.09995, abs_tol=1e-12)
               and math.isclose(trade.pnl, pnl, abs_tol=1e-10) for trade in stats.trades)


def run_dataset(size, repeats, conversion_repeats, selected):
    data = pd.DataFrame(
        {column: np.full(size, 1.1) for column in ("open", "high", "low", "close")},
        index=pd.date_range("2026-01-01", periods=size, freq="s", tz="UTC"),
    )
    backtest = Backtest(data)
    conversion = []
    for _ in range(conversion_repeats):
        start = time.perf_counter_ns()
        bars = backtest._to_bars(data)
        conversion.append((time.perf_counter_ns() - start) / 1e9)
        # Release the old list before allocating another one in the next repetition.
        if len(conversion) < conversion_repeats:
            del bars

    results = []
    available = cases()
    for name, (strategy_type, expected_trades) in available.items():
        if selected and name not in selected:
            continue
        samples = []
        # One untimed warmup, then fresh engines and strategies for every sample.
        for repetition in range(repeats + 1):
            start_construction = time.perf_counter_ns()
            engine = Engine(bars, *CONFIG)
            constructed = time.perf_counter_ns()
            strategy = strategy_type() if strategy_type else None
            if strategy is None:
                adapter = None
            elif name == "direct-noop":
                adapter = strategy
            else:
                adapter = TimedAdapter(strategy)
            start = time.perf_counter_ns()
            if adapter is None:
                stats = engine._run_native_noop()
            else:
                stats = engine.run(adapter)
            end = time.perf_counter_ns()
            setup_finished = adapter.setup_finished if adapter else start
            if repetition:
                samples.append({
                    "engine_setup_seconds": (constructed - start_construction) / 1e9,
                    "init_seconds": (setup_finished - start) / 1e9,
                    "simulation_seconds": (end - setup_finished) / 1e9,
                })
            assert_result(stats, size, size if expected_trades is None else expected_trades)
            if isinstance(strategy, (RollingIndicator, PrecomputedIndicator)):
                assert math.isclose(strategy.value, 1.1, abs_tol=1e-8)
            if isinstance(strategy, Hold):
                if isinstance(strategy, ReadPresence):
                    assert strategy.observed is True
                elif isinstance(strategy, (ReadPositions, ReadCount)):
                    assert strategy.observed == strategy.count
            del stats, engine, strategy, adapter
        results.append({"case": name, "bars": size,
                        **{key: statistics.median(sample[key] for sample in samples)
                           for key in samples[0]},
                        "simulation_samples_seconds": [s["simulation_seconds"] for s in samples]})
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_rss = rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        peak_rss = None
    return {"bars": size, "conversion_seconds": statistics.median(conversion),
            "peak_rss_bytes": peak_rss, "results": results}


def command_output(command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def metadata():
    cpu = platform.processor()
    if Path("/proc/cpuinfo").exists():
        cpu = next((line.split(":", 1)[1].strip() for line in
                    Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")), cpu)
    root = Path(__file__).resolve().parents[1]
    lock = root / "Cargo.lock"
    pyo3 = re.search(r'name = "pyo3"\nversion = "([^"]+)"', lock.read_text()) if lock.exists() else None
    return {"python": sys.version, "platform": platform.platform(), "cpu": cpu,
            "pyo3_lock_version": pyo3.group(1) if pyo3 else "unavailable",
            "rust": command_output(["rustc", "--version"]),
            "git_head": command_output(["git", "-C", str(root), "rev-parse", "HEAD"]),
            "package_versions": {name: importlib.metadata.version(name)
                                 for name in ("backtestingfx", "pandas", "numpy")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10_000, 100_000])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--conversion-repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--label", default="working-tree")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-size", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or args.conversion_repeats < 1 or any(size < 1 for size in args.sizes):
        parser.error("sizes and repeats must be positive")
    if args.cases and set(args.cases) - set(cases()):
        parser.error(f"available cases: {', '.join(cases())}")
    if args.worker_size:
        print(json.dumps(run_dataset(args.worker_size, args.repeats, args.conversion_repeats, args.cases)))
        return
    report = {"label": args.label, "metadata": metadata(), "repeats": args.repeats,
              "conversion_repeats": args.conversion_repeats,
              "datasets": []}
    for size in args.sizes:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker-size", str(size),
                   "--repeats", str(args.repeats), "--conversion-repeats", str(args.conversion_repeats)]
        if args.cases:
            command.extend(["--cases", *args.cases])
        dataset = json.loads(subprocess.check_output(command, text=True))
        report["datasets"].append(dataset)
        print(f"{size:,} bars; conversion {dataset['conversion_seconds']:.4f} s; "
              f"peak RSS {dataset['peak_rss_bytes']} bytes", flush=True)
        for row in dataset["results"]:
            print(f"  {row['case']:<23} init {row['init_seconds']:.6f} s  "
                  f"run {row['simulation_seconds']:.6f} s  "
                  f"{row['simulation_seconds'] / size * 1e9:.1f} ns/bar", flush=True)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
