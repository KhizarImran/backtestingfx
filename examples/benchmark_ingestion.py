"""Profile setup and verify matched results in fresh processes per input size.

Requires the optional backtesting==0.6.6 reference package. Run against an
installed release build from outside the checkout. See benchmark_ingestion.md.
"""

import argparse
import cProfile
import importlib.metadata
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

from backtestingfx import Backtest, Bar, Engine

from benchmark_callbacks import command_output, metadata
from compare_bt import Comparison, load_data


class InitClock:
    def init(self, bars):
        self.initialized = time.perf_counter_ns()

    def next(self, bar, broker):
        pass


def setup_sample(data):
    backtest = Backtest(data)
    clock = InitClock()
    start = time.perf_counter_ns()
    snapshot = data.copy(deep=True)
    copied = time.perf_counter_ns()
    engine = backtest._engine(snapshot)
    constructed = time.perf_counter_ns()
    stats = engine.run(clock)
    end = time.perf_counter_ns()
    times = {
        "snapshot_seconds": (copied - start) / 1e9,
        "ingestion_seconds": (constructed - copied) / 1e9,
        "history_seconds": (clock.initialized - constructed) / 1e9,
        "noop_run_seconds": (end - clock.initialized) / 1e9,
        "setup_seconds": (clock.initialized - start) / 1e9,
        "total_seconds": (end - start) / 1e9,
    }
    assert stats.num_trades == 0
    assert stats.equity_curve == [10_000.0] * (len(data) + 1)
    return times


def summarize(samples):
    return {"medians": {key: statistics.median(row[key] for row in samples)
                        for key in samples[0]}, "samples": samples}


def row_candidates(size, repeats):
    data = load_data(size)
    results = {}
    for name in ("zip-series", "itertuples", "bulk"):
        samples = []
        for repetition in range(repeats + 1):
            start = time.perf_counter_ns()
            if name == "bulk":
                engine = Backtest(data)._engine(data)
            else:
                rows = (zip(data.index, *(data[column] for column in
                                         ("open", "high", "low", "close", "volume")))
                        if name == "zip-series" else data.itertuples(index=True, name=None))
                bars = [Bar(int(index.timestamp()), float(o), float(h), float(l),
                            float(c), float(v)) for index, o, h, l, c, v in rows]
                engine = Engine(bars, 10_000.0, 0.0, 0.0, 100_000.0, 1.0)
            end = time.perf_counter_ns()
            if repetition:
                samples.append({"ingestion_seconds": (end - start) / 1e9})
            # Verify values after timing, without holding old Bar lists between samples.
            class Check:
                def init(self, history):
                    for bar, row in zip(history, data.itertuples(index=True, name=None)):
                        assert (bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume) == (
                            int(row[0].timestamp()), *row[1:])

                def next(self, bar, broker):
                    pass

            engine.run(Check())
            del engine
            if name != "bulk":
                del bars
        results[name] = summarize(samples)
    return {"bars": size, "candidates": results}


def worker(size, repeats):
    data = load_data(size)
    setup_sample(data)
    setup = [setup_sample(data) for _ in range(repeats)]
    comparison = Comparison(data, commission=7.0)
    reference, _ = comparison.run("backtesting.py")
    actual, _ = comparison.run("backtestingfx")
    comparison.verify(reference, actual)
    samples = {name: [] for name in ("backtesting.py", "backtestingfx")}
    for repetition in range(repeats):
        results = {}
        order = list(samples)
        if repetition % 2:
            order.reverse()
        for name in order:
            result, times = comparison.run(name)
            results[name] = result
            samples[name].append(times)
        comparison.verify(results["backtesting.py"], results["backtestingfx"])
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_rss = rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        peak_rss = None
    return {"bars": size, "setup_breakdown": summarize(setup),
            "matched": {name: summarize(rows) for name, rows in samples.items()},
            "matching_trades": actual.num_trades, "final_equity": actual.final_cash,
            "equivalence": "passed", "peak_rss_bytes": peak_rss}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[50_000, 100_000])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--label", default="working-tree")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", type=Path, help="write a separate cProfile of one run")
    parser.add_argument("--candidates", action="store_true",
                        help="compare Python row iteration with bulk ingestion")
    parser.add_argument("--worker-size", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 3 for size in args.sizes):
        parser.error("sizes must be at least three and repeats must be positive")
    if args.worker_size:
        measure = row_candidates if args.candidates else worker
        print(json.dumps(measure(args.worker_size, args.repeats)))
        return
    report = {"label": args.label, "metadata": metadata(), "repeats": args.repeats,
              "source_diff": command_output(["git", "-C", str(Path(__file__).resolve().parents[1]),
                                              "diff", "--stat"]), "datasets": []}
    report["metadata"]["package_versions"]["backtesting"] = importlib.metadata.version("backtesting")
    for size in args.sizes:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker-size", str(size),
                   "--repeats", str(args.repeats)]
        if args.candidates:
            command.append("--candidates")
        result = json.loads(subprocess.check_output(command, text=True))
        report["datasets"].append(result)
        if args.candidates:
            print(f"{size:,} bars: " + str({name: row["medians"]
                                           for name, row in result["candidates"].items()}), flush=True)
            continue
        print(f"PASS {size:,} bars, {result['matching_trades']} trades; "
              f"peak RSS {result['peak_rss_bytes']} bytes", flush=True)
        print("  setup breakdown:", result["setup_breakdown"]["medians"], flush=True)
        for name, times in result["matched"].items():
            print(f"  {name}: {times['medians']}", flush=True)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    if args.profile:
        # Profiling overhead is excluded from every timing and memory sample above.
        comparison = Comparison(load_data(args.sizes[0]), commission=7.0)
        profiler = cProfile.Profile()
        profiler.runcall(comparison.run, "backtestingfx")
        profiler.dump_stats(str(args.profile))


if __name__ == "__main__":
    main()
