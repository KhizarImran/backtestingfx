"""Time the matched comparison from compare_bt.py only after equivalence passes.

Run against installed release packages outside the checkout. See benchmark.md.
"""

import argparse
import importlib.metadata
import json
from pathlib import Path
import statistics
import time

from benchmark_callbacks import metadata
from compare_bt import Comparison, load_data


def measure(size, commission, repeats):
    start = time.perf_counter()
    comparison = Comparison(load_data(size), commission=commission)
    common_setup = time.perf_counter() - start
    # Untimed equivalence check and warmup before collecting any timing samples.
    reference, _ = comparison.run("backtesting.py")
    actual, _ = comparison.run("backtestingfx")
    comparison.verify(reference, actual)
    samples = {name: [] for name in ("backtesting.py", "backtestingfx")}
    for repetition in range(repeats):
        order = list(samples)
        if repetition % 2:
            order.reverse()
        results = {}
        for name in order:
            result, timings = comparison.run(name)
            results[name] = result
            samples[name].append(timings)
        # Every measured pair must also agree. Assertions are outside timing.
        comparison.verify(results["backtesting.py"], results["backtestingfx"])
    report = {"bars": size, "commission_per_lot_per_side": commission,
              "common_preparation_seconds": common_setup,
              "matching_trades": actual.num_trades, "final_equity": actual.final_cash,
              "equivalence": "passed", "libraries": {}}
    for name, times in samples.items():
        report["libraries"][name] = {
            **{key: statistics.median(row[key] for row in times) for key in times[0]},
            "samples": times,
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[2000, 10_000, 50_000])
    parser.add_argument("--commissions", type=float, nargs="+", default=[0.0, 7.0])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 3 for size in args.sizes):
        parser.error("sizes must be at least three and repeats must be positive")
    report = {"metadata": metadata(), "repeats": args.repeats, "results": []}
    report["metadata"]["package_versions"]["backtesting"] = importlib.metadata.version("backtesting")
    print("Times are medians; run includes simulation, finalization, and each library's statistics.")
    for size in args.sizes:
        for commission in args.commissions:
            result = measure(size, commission, args.repeats)
            report["results"].append(result)
            print(f"\nPASS: {size:,} bars, commission {commission}/lot/side, "
                  f"{result['matching_trades']} matching trades, equity {result['final_equity']:.8f}",
                  flush=True)
            for name, timings in result["libraries"].items():
                print(f"  {name:<15} setup {timings['setup_seconds']:.4f} s  "
                      f"run {timings['simulation_seconds']:.4f} s  "
                      f"total {timings['total_seconds']:.4f} s", flush=True)
            bt = result["libraries"]["backtesting.py"]
            fx = result["libraries"]["backtestingfx"]
            print(f"  backtestingfx speed ratio: run {bt['simulation_seconds'] / fx['simulation_seconds']:.2f}x; "
                  f"total {bt['total_seconds'] / fx['total_seconds']:.2f}x. Above 1 means faster.", flush=True)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
